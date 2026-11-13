import { Wordmark } from "@/components/ui/Wordmark";
import Link from "next/link";

/**
 * AYVID MarketingFooter — quiet wealth wordmark + disclosure block.
 *
 * Restraint over chrome: no elevated background (footer sits flush on
 * page bg), no decorative monogram, only hairline rules.  The wordmark
 * sets in display serif and reads as the colophon of a printed report.
 */

const FOOTER_LINKS: {
  heading: string;
  items: { href: string; label: string }[];
}[] = [
  {
    heading: "Company",
    items: [
      { href: "/about", label: "About" },
      { href: "/approach", label: "Approach" },
      { href: "/research", label: "Research workspace" },
      { href: "/notes", label: "Research notes" },
      { href: "/contact", label: "Contact" },
    ],
  },
  {
    heading: "Disclosures",
    items: [
      { href: "/about#legal", label: "Research only · no orders" },
      { href: "/about#legal", label: "Not investment advice" },
    ],
  },
  {
    heading: "Get access",
    items: [
      { href: "/contact", label: "Request access" },
      { href: "/contact", label: "Join us" },
      { href: "/console/live-shock?demo=1", label: "Shock Lab (paper only)" },
    ],
  },
];

export function MarketingFooter() {
  const year = new Date().getFullYear();
  return (
    <footer
      style={{
        borderTop: "1px solid var(--rule)",
        background: "hsl(var(--bg))",
      }}
    >
      <div
        className="container-page mx-auto"
        style={{ paddingTop: 64, paddingBottom: 48 }}
      >
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr",
            gap: 48,
          }}
          className="footer-grid"
        >
          <div>
            <Link
              href="/"
              aria-label="AYVID home"
              style={{ display: "inline-block", textDecoration: "none", opacity: 0.86, marginBottom: 16 }}
            >
              <Wordmark height={28} />
            </Link>
            <p
              className="body-prose"
              style={{ maxWidth: "32ch" }}
            >
              An AI-native financial research workspace. Cited answers,
              second-order exposure analysis, and monitored theses.
            </p>
          </div>

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(4, 1fr)",
              gap: 32,
            }}
            className="footer-cols"
          >
            {FOOTER_LINKS.map((section) => (
              <div key={section.heading}>
                <p
                  className="label-mono"
                  style={{ marginBottom: 16, color: "hsl(var(--fg-subtle))" }}
                >
                  {section.heading}
                </p>
                <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
                  {section.items.map((item) => (
                    <li key={item.label} style={{ marginBottom: 8 }}>
                      <Link
                        href={item.href}
                        className="footer-link"
                      >
                        {item.label}
                      </Link>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
            <div>
              <p
                className="label-mono"
                style={{ marginBottom: 16, color: "hsl(var(--fg-subtle))" }}
              >
                Offices
              </p>
              <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
                <li
                  style={{
                    fontFamily:
                      'var(--font-sans), "Inter", system-ui, sans-serif',
                    fontSize: 13,
                    color: "hsl(var(--fg-muted))",
                    marginBottom: 8,
                  }}
                >
                  New York
                </li>
                <li
                  style={{
                    fontFamily:
                      'var(--font-sans), "Inter", system-ui, sans-serif',
                    fontSize: 13,
                    color: "hsl(var(--fg-muted))",
                  }}
                >
                  Bengaluru
                </li>
              </ul>
            </div>
          </div>
        </div>

        <div
          style={{
            marginTop: 64,
            paddingTop: 24,
            borderTop: "1px solid var(--rule)",
            display: "flex",
            flexWrap: "wrap",
            gap: 16,
            justifyContent: "space-between",
            alignItems: "flex-start",
          }}
        >
          <p
            style={{
              fontFamily:
                'var(--font-sans), "Inter", system-ui, sans-serif',
              fontSize: 11,
              lineHeight: 1.65,
              color: "hsl(var(--fg-subtle))",
              maxWidth: "62ch",
            }}
          >
            © {year} AYVID. AYVID is a research tool and is not a registered
            investment adviser. Nothing on this site is investment advice or an
            offer to buy or sell any security. Historical and simulated results
            are not indicative of future results.
          </p>
          <p
            className="label-mono"
            style={{ color: "hsl(var(--fg-subtle))" }}
          >
            Research only · not investment advice
          </p>
        </div>
      </div>

      <style>{`
        .footer-link {
          font-family: var(--font-sans), "Inter", system-ui, sans-serif;
          font-size: 13px;
          color: hsl(var(--fg-muted));
          transition: color var(--t-quick) var(--ease-monument);
        }
        .footer-link:hover {
          color: hsl(var(--fg));
        }
        @media (min-width: 900px) {
          .footer-grid {
            grid-template-columns: 1fr 2fr !important;
          }
        }
        @media (max-width: 720px) {
          .footer-cols {
            grid-template-columns: repeat(2, 1fr) !important;
            row-gap: 32px;
          }
        }
      `}</style>
    </footer>
  );
}
