// Keep a begun Application change and its UI commit ahead of New. Resolution and
// refresh happen outside this queue, so pending synchronization never locks New.
export class WorkChangeQueue {
  private generation = 0;
  private pending: Promise<unknown> = Promise.resolve();

  capture() { return this.generation; }
  isCurrent(generation: number) { return generation === this.generation; }

  resolve(generation: number, operation: () => Promise<void>) {
    return this.enqueue(async () => {
      if (this.isCurrent(generation)) await operation();
    });
  }

  reset<T>(operation: () => Promise<T>): Promise<T> {
    this.generation++;
    return this.enqueue(operation);
  }

  private enqueue<T>(operation: () => Promise<T>): Promise<T> {
    const next = this.pending.then(operation);
    this.pending = next.catch(() => undefined);
    return next;
  }
}
