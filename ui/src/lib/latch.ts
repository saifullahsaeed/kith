/**
 * "This has been true at least once", for the life of a mount.
 *
 * Written for one problem and named for the shape, because the shape recurs: a component that
 * renders a *different element type* depending on a condition destroys everything below it each
 * time the condition flips. React decides identity by type and position, so `cond ? <A/> :
 * <B>{children}</B>` is not "the same thing, optionally wrapped" — it is two components sharing a
 * slot, and the children are rebuilt on every changeover, losing whatever state they held.
 *
 * Where a wrapper is *earned* rather than merely toggled, latching is the honest answer: the thing
 * became a disclosure, and it does not stop being one just because the reason it became one has
 * passed. The alternative — flipping back for the tidier resting look — costs the reader whatever
 * they had opened, which is a worse trade than a header that outstays its usefulness.
 *
 * Per mount, deliberately. Something rendered fresh from stored data never saw the condition be
 * true, so it draws the resting shape, which is what it should be.
 *
 * Writing a ref during render is safe here and only here: the write is monotonic and idempotent, so
 * a double render under StrictMode reaches the same answer.
 */
import { useRef } from "react";

export function useLatched(now: boolean): boolean {
  const ever = useRef(false);
  if (now) ever.current = true;
  return ever.current;
}
