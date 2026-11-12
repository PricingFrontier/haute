/** The class names given, joined; a false or missing one is left out. */
export const classNames = (...names: (string | false | undefined)[]) => names.filter(Boolean).join(" ")
