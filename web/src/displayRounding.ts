/**
 * Round half-up with the same decimal semantics as Python's round_half_up().
 * Exact integer arithmetic avoids binary floating-point halfway errors.
 */
export function roundHalfUp(value: number, quantum: number): number {
  const parseDecimal = (raw: string) => {
    const trimmed = raw.trim();
    const sign = trimmed.startsWith("-") ? -1 : 1;
    const unsigned = trimmed.replace(/^[+-]/, "");
    const [integer = "0", fraction = ""] = unsigned.split(".");
    return { sign, integer, fraction, scale: fraction.length };
  };

  const parsedValue = parseDecimal(value.toString());
  const parsedQuantum = parseDecimal(quantum.toString());
  const scale = Math.max(parsedValue.scale, parsedQuantum.scale);
  const valueInteger = BigInt(
    parsedValue.integer + parsedValue.fraction.padEnd(scale, "0"),
  );
  const quantumInteger = BigInt(
    parsedQuantum.integer + parsedQuantum.fraction.padEnd(scale, "0"),
  );
  const absoluteValue = valueInteger < 0n ? -valueInteger : valueInteger;
  const absoluteQuantum = quantumInteger < 0n ? -quantumInteger : quantumInteger;
  const quotient = absoluteValue / absoluteQuantum;
  const remainder = absoluteValue % absoluteQuantum;
  const rounded = remainder * 2n >= absoluteQuantum ? quotient + 1n : quotient;
  const signed = parsedValue.sign * parsedQuantum.sign < 0 ? -rounded : rounded;
  const resultInteger = signed * quantumInteger;
  const resultText = resultInteger.toString();
  const resultSign = resultText.startsWith("-") ? "-" : "";
  const resultUnsigned = resultText.replace(/^-/, "");

  let decimal: string;
  if (scale === 0) {
    decimal = resultSign + resultUnsigned;
  } else {
    const padded = resultUnsigned.padStart(scale + 1, "0");
    const integer = padded.slice(0, padded.length - scale) || "0";
    const fraction = padded.slice(padded.length - scale);
    decimal = `${resultSign}${integer}.${fraction}`;
  }

  const result = Number.parseFloat(decimal);
  return Object.is(result, -0) ? 0 : result;
}

/** Format the whole-degree operational MC while displaying north as 360. */
export function formatOperationalMagneticCourse(value: number): string {
  const rounded = roundHalfUp(value, 1);
  const normalized = ((rounded % 360) + 360) % 360;
  return normalized === 0 ? "360" : normalized.toFixed(0).padStart(3, "0");
}
