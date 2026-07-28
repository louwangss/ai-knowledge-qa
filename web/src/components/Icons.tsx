import type { SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement>;

function IconBase({ children, ...props }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true" {...props}>
      {children}
    </svg>
  );
}

export function PlusIcon(props: IconProps) {
  return <IconBase {...props}><path d="M12 5v14M5 12h14" /></IconBase>;
}

export function SearchIcon(props: IconProps) {
  return <IconBase {...props}><circle cx="11" cy="11" r="6.5" /><path d="m16 16 4 4" /></IconBase>;
}

export function NoteIcon(props: IconProps) {
  return <IconBase {...props}><path d="M6 3.5h9l3 3v14H6z" /><path d="M15 3.5v4h3M9 12h6M9 16h5" /></IconBase>;
}

export function TrashIcon(props: IconProps) {
  return <IconBase {...props}><path d="M4 7h16M9 3.5h6L16 7H8zM7 7l1 13h8l1-13M10 11v5M14 11v5" /></IconBase>;
}

export function MenuIcon(props: IconProps) {
  return <IconBase {...props}><path d="M4 7h16M4 12h16M4 17h16" /></IconBase>;
}

export function ArrowIcon(props: IconProps) {
  return <IconBase {...props}><path d="m9 18 6-6-6-6" /></IconBase>;
}

export function ExternalIcon(props: IconProps) {
  return <IconBase {...props}><path d="M14 5h5v5M19 5l-8 8" /><path d="M17 13v6H5V7h6" /></IconBase>;
}
