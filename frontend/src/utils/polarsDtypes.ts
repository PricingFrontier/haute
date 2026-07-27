/** Whether a Polars dtype is one of the scalar numeric types. */
export function isNumericDtype(dtype: string): boolean {
  const normalised = dtype.toLowerCase()
  return normalised.startsWith("int") || normalised.startsWith("uint") || normalised.startsWith("float") || normalised === "f32" || normalised === "f64" || normalised === "i8" || normalised === "i16" || normalised === "i32" || normalised === "i64" || normalised === "u8" || normalised === "u16" || normalised === "u32" || normalised === "u64"
}
