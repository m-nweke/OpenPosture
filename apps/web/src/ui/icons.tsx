/**
 * The rail's icon set: small, single-weight line glyphs, 20x20, stroked in `currentColor`.
 *
 * Hand-rolled rather than a library. Six icons do not justify a dependency, and a shared stroke
 * width/cap/join across all of them is what makes an icon-only rail read as one alphabet instead
 * of six mismatched glyphs.
 */

interface IconProps {
  className?: string
}

const common = {
  width: 20,
  height: 20,
  viewBox: '0 0 20 20',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.6,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
  'aria-hidden': true,
}

export function HomeIcon({ className }: IconProps) {
  return (
    <svg {...common} className={className}>
      <path d="M3 9.5 10 3l7 6.5" />
      <path d="M5 8.5V17h10V8.5" />
    </svg>
  )
}

export function UploadIcon({ className }: IconProps) {
  return (
    <svg {...common} className={className}>
      <rect x="3" y="4" width="14" height="12" rx="2" />
      <path d="M10 13V7M7.5 9.5 10 7l2.5 2.5" />
    </svg>
  )
}

export function HistoryIcon({ className }: IconProps) {
  return (
    <svg {...common} className={className}>
      <circle cx="10" cy="10.5" r="6.5" />
      <path d="M10 7v3.5l2.5 1.5" />
      <path d="M6 2.5 3.5 4.5M14 2.5 16.5 4.5" />
    </svg>
  )
}

export function SignOutIcon({ className }: IconProps) {
  return (
    <svg {...common} className={className}>
      <path d="M8 3H4.5a1 1 0 0 0-1 1v12a1 1 0 0 0 1 1H8" />
      <path d="M9 10h8M17 10l-3-3M17 10l-3 3" />
    </svg>
  )
}

export function SignInIcon({ className }: IconProps) {
  return (
    <svg {...common} className={className}>
      <path d="M12 3h3.5a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H12" />
      <path d="M11 10H3M3 10l3-3M3 10l3 3" />
    </svg>
  )
}

export function RegisterIcon({ className }: IconProps) {
  return (
    <svg {...common} className={className}>
      <circle cx="8" cy="7.5" r="3" />
      <path d="M2.5 17c.7-3 2.9-4.5 5.5-4.5s4.8 1.5 5.5 4.5" />
      <path d="M15.5 6.5v4M13.5 8.5h4" />
    </svg>
  )
}
