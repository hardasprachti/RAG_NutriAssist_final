"use client";

interface Props {
  /** The number shown in the sources panel for the cited passage. */
  number: number | string;
  label: string;
  active: boolean;
  onClick: () => void;
}

/** A numbered pill after a claim; selecting it highlights the supporting passage in the sources panel. */
export default function CitationBadge({ number, label, active, onClick }: Props) {
  return (
    <button
      type="button"
      className={`badge${active ? " on" : ""}`}
      aria-label={label}
      aria-pressed={active}
      title={label}
      onClick={onClick}
    >
      {number}
    </button>
  );
}
