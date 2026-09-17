/**
 * The document, as a list you can point at.
 *
 * Earns its place for the nodes a pointer cannot reach: a frame with no padding of its own is
 * covered entirely by its children, so clicking it in the canvas selects something else every
 * time. The tree is how you get hold of a container in order to change the thing — gap, padding,
 * direction — that only containers have.
 *
 * Named by `data-k`, which is the same name the inspector shows and the same one written into
 * the file, so there is one vocabulary for a node rather than three.
 */
import type { DesignNode } from "@/lib/design/parse";
import { cn } from "@/lib/utils";

export interface DesignLayersProps {
  root: DesignNode;
  selected: string | null;
  onSelect: (id: string) => void;
}

export function DesignLayers({ root, selected, onSelect }: DesignLayersProps) {
  return (
    <ul className="flex flex-col py-2 text-xs">
      {root.children.map((child) => (
        <Branch key={child.id} node={child} depth={0} selected={selected} onSelect={onSelect} />
      ))}
    </ul>
  );
}

function Branch({
  node,
  depth,
  selected,
  onSelect,
}: {
  node: DesignNode;
  depth: number;
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  // A minted id says nothing, so a node he did not name is shown by what it is and what it says.
  const minted = /^k\d+$/.test(node.id);
  const label = minted ? node.tag : node.id;
  const hint = minted ? (node.text ?? node.classes.join(".")) : node.tag;

  return (
    <li>
      <button
        type="button"
        onClick={() => onSelect(node.id)}
        style={{ paddingInlineStart: `${depth * 12 + 12}px` }}
        className={cn(
          "flex w-full items-baseline gap-2 py-1 pe-3 text-start transition-colors hover:bg-accent/60",
          selected === node.id && "bg-kith-soft text-kith",
        )}
      >
        <span className="truncate font-medium">{label}</span>
        {hint ? <span className="truncate text-[10px] text-muted-foreground">{hint}</span> : null}
      </button>
      {node.children.length ? (
        <ul className="flex flex-col">
          {node.children.map((child) => (
            <Branch key={child.id} node={child} depth={depth + 1} selected={selected} onSelect={onSelect} />
          ))}
        </ul>
      ) : null}
    </li>
  );
}
