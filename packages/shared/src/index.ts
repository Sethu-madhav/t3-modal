/** Types and constants shared by the laptop service and the daemon. */

/** The tag key the controller puts on every sandbox, with the machine id as its value (implementation.md §6.4). */
export const SANDBOX_TAG: string = "t3modal";

/** The machine sizes t3modal offers (implementation.md §13). */
export type Size = "small" | "default" | "large";
