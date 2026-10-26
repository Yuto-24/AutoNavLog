import { API, COOLDOWN, DAILY_LIMIT } from './worker.ts';

interface Ledger { day: string; attempts: number; lastAttempt: number; reservations?: number[] }
interface Context {
  storage: { get<T>(key: string): Promise<T | undefined>; put(key: string, value: unknown): Promise<void> };
  blockConcurrencyWhile<T>(callback: () => Promise<T>): Promise<T>;
}
interface GateEnv { MODE: string; GITHUB_TOKEN?: string }
/** One SQLite Durable Object serializes every fallback POST; KV is only a migration hint. */
export class RequestGate {
  private ctx: Context;
  private env: GateEnv;
  private send: typeof fetch;
  private clock: () => number;
  constructor(ctx: Context, env: GateEnv, send: typeof fetch = fetch, clock = Date.now) {
    this.ctx = ctx; this.env = env; this.send = (input, init) => send(input, init); this.clock = () => clock();
  }
  /** Reserve durably before one POST, retaining the slot on transport or storage failure. */
  async fetch(req: Request): Promise<Response> {
    return this.ctx.blockConcurrencyWhile(async () => {
      if (this.env.MODE !== 'dispatch' || !this.env.GITHUB_TOKEN) throw Error('GATE_DISABLED');
      const input: Ledger = await req.json();
      const now = this.clock(), day = new Date(now).toISOString().slice(0, 10);
      const saved = await this.ctx.storage.get<Ledger>('requests');
      const valid = (value: Ledger) => /^\d{4}-\d{2}-\d{2}$/.test(value.day) &&
        Number.isSafeInteger(value.attempts) && value.attempts >= 0 &&
        Number.isFinite(value.lastAttempt) && value.lastAttempt >= 0;
      if (!valid(input) || (saved && !valid(saved))) throw Error('INVALID_GATE_STATE');
      let reservations = saved?.reservations ?? [];
      if (!Array.isArray(reservations) || reservations.some(t => !Number.isFinite(t) || t < 0)) throw Error('INVALID_GATE_STATE');
      reservations = reservations.filter(t => now - t < 24 * 3600_000);
      const attempts = Math.max(input.day === day ? input.attempts : 0, saved?.day === day ? saved.attempts : 0);
      const lastAttempt = Math.max(input.lastAttempt, saved?.lastAttempt ?? 0);
      // Import legacy evidence durably even when this invocation will be rejected.
      // Otherwise a later stale KV/history read could forget a limit or cooldown.
      const today = reservations.filter(t => new Date(t).toISOString().startsWith(day)).length;
      const missing = Math.max(0, Math.min(attempts, DAILY_LIMIT) - today);
      for (let i = 0; i < missing; i++) reservations.push(now + 10_000);
      if (!saved || missing || saved.day !== day || saved.attempts !== attempts || saved.lastAttempt !== lastAttempt) {
        await this.ctx.storage.put('requests', { day, attempts, lastAttempt, reservations });
      }
      if (now - lastAttempt < COOLDOWN) return Response.json({ status: 'COOLDOWN' });
      if (attempts >= DAILY_LIMIT || reservations.length >= DAILY_LIMIT) return Response.json({ status: 'DAILY_LIMIT' });
      // Reserve through the maximum POST duration, even if execution crashes after send.
      const ledger = { day, attempts: attempts + 1, lastAttempt: now + 10_000,
        reservations: [...reservations, now + 10_000] };
      await this.ctx.storage.put('requests', ledger);
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 10_000);
      let status: number | "IO_ERROR" = "IO_ERROR";
      try {
        // A slow durable write must not create a late, unaccounted POST.
        if (this.clock() > ledger.lastAttempt) throw Error('RESERVATION_EXPIRED');
        const response = await this.send(`${API}/dispatches`, {
          method: 'POST', redirect: 'manual', signal: controller.signal,
          headers: { Authorization: `Bearer ${this.env.GITHUB_TOKEN}`, Accept: 'application/vnd.github+json',
            'User-Agent': 'AutoNavLog-MSM-trigger', 'X-GitHub-Api-Version': '2026-03-10', 'Content-Type': 'application/json' },
          body: JSON.stringify({ ref: 'main', inputs: { feed_refresh: true, request_id: String(now), publish: true } }),
        });
        status = response.status;
        if (response.status !== 204) throw Error('DISPATCH_REJECTED');
        return Response.json({ status: 'DISPATCH_ACCEPTED', ...ledger });
      } catch {
        // No retry. Reservation remains after an ambiguous POST; never expose provider details.
        throw Error('DISPATCH_UNCONFIRMED');
      } finally {
        clearTimeout(timeout);
        console.log(JSON.stringify({ stage: 'dispatch', target: 'api.github.com', status, bytes: 0 }));
      }
    });
  }
}
