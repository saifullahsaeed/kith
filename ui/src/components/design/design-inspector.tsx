/**
 * The panel that turns a selected node into things you can change.
 *
 * Two decisions here are the design, not the implementation.
 *
 * **Colour is a list of tokens before it is a text field.** A dropdown of nine named colours and
 * a custom box that warns is not a convenience — it is the only reason the token dialect holds.
 * He is told to write `var(--kith-accent)`, and an inspector offering a free-text colour field
 * would quietly undo that the first time anybody nudged a heading, because typing `#f1b65a` is
 * easier than remembering which token that is. A design full of literal hex stops following the
 * theme and stops looking like the rest of his work, and nothing would ever have failed.
 *
 * **Layout is padding and gap, not width and height.** The dialect has no coordinates in it, so
 * the fields that would express coordinates are absent: what a flex container actually has is a
 * direction, a gap, a padding and two alignments. Offering `top`/`left` here would invite a
 * design he cannot edit afterwards — see `design-page.tsx` for the same argument about dragging.
 */
import { AlignCenterHorizontal, Baseline, Box, Component, Palette, Square, Unlink } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

import { Dropdown } from "@/components/ui/dropdown";
import type { DesignNode } from "@/lib/design/parse";
import { type System, swatches } from "@/lib/design/system";
import { cn } from "@/lib/utils";

/** `null` removes the declaration. */
export type StyleChange = (property: string, value: string | null) => void;

export interface DesignInspectorProps {
  node: DesignNode | null;
  onChange: StyleChange;
  /** The palette and type scale this design is drawn in — his, not Kith's. */
  system: System;
  /** The instance whose part the node came from, or the node itself when it is an instance. */
  owner?: DesignNode | null;
  /** Open a part's own file, showing its holes with these props. */
  onEditPart?: (name: string, props: Record<string, string>) => void;
  /** Turn an instance on this screen back into plain markup, so it can differ from the rest. */
  onDetach?: (id: string) => void;
}

const DIRECTIONS = [
  { value: "row", label: "Row" },
  { value: "column", label: "Column" },
];

const ALIGN = [
  { value: "stretch", label: "Stretch" },
  { value: "flex-start", label: "Start" },
  { value: "center", label: "Centre" },
  { value: "flex-end", label: "End" },
];

const JUSTIFY = [
  { value: "flex-start", label: "Start" },
  { value: "center", label: "Centre" },
  { value: "flex-end", label: "End" },
  { value: "space-between", label: "Space between" },
];

const WEIGHTS = [
  { value: "400", label: "Regular" },
  { value: "500", label: "Medium" },
  { value: "600", label: "Semibold" },
  { value: "700", label: "Bold" },
];

const ALIGN_TEXT = [
  { value: "left", label: "Left" },
  { value: "center", label: "Centre" },
  { value: "right", label: "Right" },
];

export function DesignInspector({ node, onChange, system, owner, onEditPart, onDetach }: DesignInspectorProps) {
  const palette = swatches(system);

  if (!node) {
    return (
      <div className="flex h-full items-center justify-center px-6 text-center text-sm text-muted-foreground">
        Pick something in the design.
      </div>
    );
  }

  /* A node from a part's file has no bytes in this one, so there is nothing here to edit — and
   * pretending otherwise is the failure Pace's generator had: an edit that looks applied and
   * is gone by the next build. The two honest choices are the part's own file, which changes
   * every screen, and detaching this one instance, which changes only this screen. A part used
   * inside another part can only be changed through the outer one, so detach is not offered. */
  if (node.part || node.synthetic) {
    const name = owner?.instance?.name ?? node.part?.component ?? "";
    const screenLevel = Boolean(owner && !owner.part);
    return (
      <div className="flex flex-col gap-4 p-4">
        <div>
          <div className="truncate text-sm font-medium">{node.part?.local ?? node.id}</div>
          <div className="text-xs text-muted-foreground">{node.tag}</div>
        </div>
        <PartNotice
          title={`Inside ${name}`}
          body={
            node.synthetic
              ? "This text is written inside the component's tag on this screen. Change it in the source."
              : `This comes from the ${name} component. Change it there and every screen that uses it changes; detach this one to change it here only.`
          }
        >
          {owner?.instance && onEditPart ? (
            <button type="button" className={PART_BUTTON} onClick={() => onEditPart(owner.instance!.name, owner.instance!.props)}>
              <Component className="size-3.5" />
              Edit {name}
            </button>
          ) : null}
          {screenLevel && onDetach && !node.synthetic ? (
            <button type="button" className={PART_BUTTON} onClick={() => onDetach(owner!.id)}>
              <Unlink className="size-3.5" />
              Detach this one
            </button>
          ) : null}
        </PartNotice>
      </div>
    );
  }

  const style = node.style;
  const isFlex = style.display === "flex" || style.display === "inline-flex";
  const instance = node.instance;

  return (
    <div className="flex flex-col gap-5 p-4">
      <div>
        <div className="truncate text-sm font-medium">{node.id}</div>
        <div className="text-xs text-muted-foreground">
          {node.tag}
          {node.classes.length ? ` · .${node.classes.join(".")}` : ""}
        </div>
      </div>

      {instance ? (
        <PartNotice
          title={instance.missing ? `${instance.name} is not in the design system` : `Component · ${instance.name}`}
          body={
            instance.missing
              ? "Add it with update_design_system, or this screen keeps drawing a placeholder."
              : "Spacing and size you set here apply to this screen only. To change the component itself, edit it."
          }
        >
          {!instance.missing && onEditPart ? (
            <button type="button" className={PART_BUTTON} onClick={() => onEditPart(instance.name, instance.props)}>
              <Component className="size-3.5" />
              Edit {instance.name}
            </button>
          ) : null}
          {!instance.missing && onDetach ? (
            <button type="button" className={PART_BUTTON} onClick={() => onDetach(node.id)}>
              <Unlink className="size-3.5" />
              Detach
            </button>
          ) : null}
        </PartNotice>
      ) : null}

      <Group icon={<Box className="size-3.5" />} title="Layout">
        <Row label="Display">
          <Dropdown
            value={style.display ?? "block"}
            onChange={(value) => onChange("display", value === "block" ? null : value)}
            options={[
              { value: "block", label: "Block" },
              { value: "flex", label: "Flex" },
              { value: "inline-flex", label: "Inline flex" },
              { value: "none", label: "Hidden" },
            ]}
          />
        </Row>
        {isFlex ? (
          <>
            <Row label="Direction">
              <Dropdown
                value={style["flex-direction"] ?? "row"}
                onChange={(value) => onChange("flex-direction", value === "row" ? null : value)}
                options={DIRECTIONS}
              />
            </Row>
            <Row label="Gap">
              <Length value={style.gap} onChange={(value) => onChange("gap", value)} />
            </Row>
            <Row label="Align">
              <Dropdown
                value={style["align-items"] ?? "stretch"}
                onChange={(value) => onChange("align-items", value === "stretch" ? null : value)}
                options={ALIGN}
              />
            </Row>
            <Row label="Justify">
              <Dropdown
                value={style["justify-content"] ?? "flex-start"}
                onChange={(value) => onChange("justify-content", value === "flex-start" ? null : value)}
                options={JUSTIFY}
              />
            </Row>
          </>
        ) : null}
        <Row label="Padding">
          <Length value={style.padding} onChange={(value) => onChange("padding", value)} />
        </Row>
        <Row label="Margin">
          <Length value={style.margin} onChange={(value) => onChange("margin", value)} />
        </Row>
      </Group>

      <Group icon={<Square className="size-3.5" />} title="Size">
        <Row label="Width">
          <Length value={style.width} onChange={(value) => onChange("width", value)} />
        </Row>
        <Row label="Height">
          <Length value={style.height} onChange={(value) => onChange("height", value)} />
        </Row>
      </Group>

      <Group icon={<Baseline className="size-3.5" />} title="Text">
        <Row label="Size">
          <Length value={style["font-size"]} onChange={(value) => onChange("font-size", value)} />
        </Row>
        <Row label="Weight">
          <Dropdown
            value={style["font-weight"] ?? "400"}
            onChange={(value) => onChange("font-weight", value === "400" ? null : value)}
            options={WEIGHTS}
          />
        </Row>
        <Row label="Align">
          <Dropdown
            value={style["text-align"] ?? "left"}
            onChange={(value) => onChange("text-align", value === "left" ? null : value)}
            options={ALIGN_TEXT}
          />
        </Row>
        <Row label="Line height">
          <Length value={style["line-height"]} onChange={(value) => onChange("line-height", value)} />
        </Row>
      </Group>

      <Group icon={<Palette className="size-3.5" />} title={system.name}>
        <Row label="Text">
          <Tokens palette={palette} value={style.color} onChange={(value) => onChange("color", value)} />
        </Row>
        <Row label="Background">
          <Tokens
            palette={palette}
            value={style["background-color"] ?? style.background}
            onChange={(value) => onChange(style.background && !style["background-color"] ? "background" : "background-color", value)}
          />
        </Row>
      </Group>

      <Group icon={<AlignCenterHorizontal className="size-3.5" />} title="Border">
        <Row label="Radius">
          <Length value={style["border-radius"]} onChange={(value) => onChange("border-radius", value)} />
        </Row>
        <Row label="Border">
          <Text
            value={style.border}
            placeholder="1px solid var(--kith-line)"
            onChange={(value) => onChange("border", value)}
          />
        </Row>
      </Group>
    </div>
  );
}

function Group({ icon, title, children }: { icon: ReactNode; title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-2">
      <h3 className="flex items-center gap-1.5 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
        {icon}
        {title}
      </h3>
      <div className="flex flex-col gap-1.5">{children}</div>
    </section>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="grid grid-cols-[5.5rem_1fr] items-center gap-2">
      <span className="text-xs text-muted-foreground">{label}</span>
      {children}
    </label>
  );
}

const FIELD =
  "h-8 w-full rounded-lg border border-border/70 bg-card px-2 text-xs outline-none focus:border-kith";

/**
 * A field you can actually type in.
 *
 * Held locally and committed on blur or Enter, rather than firing on every keystroke. The
 * obvious version — value straight from the node, `onChange` per character — cannot work here,
 * because committing rewrites the document and the value comes back *normalised*: type `2` into
 * a length and it is stored as `2px`, so the `4` you type next lands after the unit and you have
 * written `2px4`. Every field in this panel would have been unusable in the same way.
 */
function Field({
  value,
  placeholder,
  commit,
  normalise,
  className,
}: {
  value?: string;
  placeholder?: string;
  commit: (value: string | null) => void;
  normalise?: (text: string) => string;
  className?: string;
}) {
  const [text, setText] = useState(value ?? "");
  const [typing, setTyping] = useState(false);

  // While you are in the field it is yours; the moment you leave, it follows the document again —
  // which is what makes a change made in the canvas or the layers tree show up here.
  useEffect(() => {
    if (!typing) setText(value ?? "");
  }, [value, typing]);

  const send = () => {
    setTyping(false);
    const clean = text.trim();
    if (clean === (value ?? "").trim()) return;
    commit(clean ? (normalise ? normalise(clean) : clean) : null);
  };

  return (
    <input
      className={cn(FIELD, className)}
      value={text}
      placeholder={placeholder}
      onFocus={() => setTyping(true)}
      onChange={(event) => setText(event.target.value)}
      onBlur={send}
      onKeyDown={(event) => {
        if (event.key === "Enter") send();
        if (event.key === "Escape") {
          setTyping(false);
          setText(value ?? "");
        }
      }}
    />
  );
}

function Text({
  value,
  placeholder,
  onChange,
}: {
  value?: string;
  placeholder?: string;
  onChange: (value: string | null) => void;
}) {
  return <Field value={value} placeholder={placeholder} commit={onChange} />;
}

/**
 * A length, typed.
 *
 * A bare number is read as pixels, because that is what everyone types and refusing it would
 * mean an inspector that quietly ignores half of what you enter.
 */
function Length({ value, onChange }: { value?: string; onChange: (value: string | null) => void }) {
  return (
    <Field
      value={value}
      placeholder="auto"
      commit={onChange}
      normalise={(text) => (/^-?[\d.]+$/.test(text) ? `${text}px` : text)}
    />
  );
}

/**
 * The palette, as the thing you pick from.
 *
 * The custom field is present and deliberately second: an escape hatch you have to reach for,
 * rather than the default path, so the token stays the easy answer.
 */
function Tokens({
  palette,
  value,
  onChange,
}: {
  palette: { name: string; value: string; reference: string }[];
  value?: string;
  onChange: (value: string | null) => void;
}) {
  const current = value ?? "";
  const named = current.startsWith("var(--");
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex flex-wrap gap-1">
        {palette.map((token) => (
          <button
            key={token.name}
            type="button"
            title={`${token.name} — ${token.value}`}
            aria-label={token.name}
            onClick={() => onChange(current === token.reference ? null : token.reference)}
            style={{ background: token.value }}
            className={cn(
              "size-5 rounded-md border border-border/70 transition-transform hover:scale-110",
              current === token.reference && "ring-2 ring-kith ring-offset-1 ring-offset-background",
            )}
          />
        ))}
      </div>
      <Field
        className="h-7"
        value={named ? "" : current}
        placeholder="custom — will not follow the system"
        commit={onChange}
      />
    </div>
  );
}

const PART_BUTTON =
  "flex items-center gap-1.5 rounded-md border border-border/70 bg-background px-2.5 py-1 text-xs font-medium transition-colors hover:bg-accent";

/** Where a node's edits go, said before anybody makes one. */
function PartNotice({ title, body, children }: { title: string; body: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-kith/30 bg-kith-soft/40 p-3">
      <div className="flex items-center gap-1.5 text-xs font-medium text-kith">
        <Component className="size-3.5" />
        {title}
      </div>
      <p className="text-xs leading-relaxed text-muted-foreground">{body}</p>
      {children ? <div className="flex flex-wrap gap-1.5">{children}</div> : null}
    </div>
  );
}
