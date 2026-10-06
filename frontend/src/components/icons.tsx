import type { ReactNode, SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement>;

function Icon({ children, ...props }: IconProps & { children: ReactNode }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      width="1em"
      height="1em"
      {...props}
    >
      {children}
    </svg>
  );
}

export const PlusIcon = (p: IconProps) => (
  <Icon strokeWidth={2.5} {...p}>
    <path d="M12 4v16m8-8H4" />
  </Icon>
);

export const MenuIcon = (p: IconProps) => (
  <Icon strokeWidth={2.5} {...p}>
    <path d="M4 6h16M4 12h16M4 18h16" />
  </Icon>
);

export const HistoryIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
  </Icon>
);

export const BookIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M4 5a2 2 0 012-2h13v16H6a2 2 0 00-2 2V5z" />
    <path d="M19 19v2H6a2 2 0 01-2-2" />
  </Icon>
);

export const CloseIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M6 18L18 6M6 6l12 12" />
  </Icon>
);

export const ExternalIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
  </Icon>
);

export const CheckCircleIcon = (p: IconProps) => (
  <Icon {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="M8.5 12.5l2.5 2.5 4.5-5" />
  </Icon>
);

export const HeartIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M12 20s-7-4.4-7-10a4 4 0 017-2.5A4 4 0 0119 10c0 5.6-7 10-7 10z" />
  </Icon>
);

export const InfoIcon = (p: IconProps) => (
  <Icon {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 11v5M12 8h.01" />
  </Icon>
);

export const AlertIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M12 3l10 18H2L12 3z" />
    <path d="M12 10v5M12 18h.01" />
  </Icon>
);

export const CopyIcon = (p: IconProps) => (
  <Icon {...p}>
    <rect x="9" y="9" width="11" height="11" rx="2" />
    <path d="M5 15V6a2 2 0 012-2h9" />
  </Icon>
);

export const SendIcon = (p: IconProps) => (
  <Icon strokeWidth={2.2} {...p}>
    <path d="M5 12h14M13 6l6 6-6 6" />
  </Icon>
);

export const PencilIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M4 20h4L19 9a2.8 2.8 0 00-4-4L4 16v4z" />
  </Icon>
);

export const TrashIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" />
  </Icon>
);

export const BowlIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M12 21a9 9 0 009-9H3a9 9 0 009 9z" fill="currentColor" fillOpacity={0.25} />
    <path d="M12 3a6 6 0 016 6H6a6 6 0 016-6z" fill="currentColor" fillOpacity={0.15} />
    <path d="M12 3v9M9 7c1.5-2 4.5-2 6 0" />
  </Icon>
);
