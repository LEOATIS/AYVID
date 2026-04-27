import Link from "next/link";
import { ArrowUpRight } from "lucide-react";

export const metadata = {
  title: "Contact",
  description:
    "Limited partner enquiries, Y Combinator partner enquiries, and operations console access requests.",
};

const CONTACTS: { audience: string; email: string; description: string }[] = [
  {
    audience: "Limited partners",
    email: "lp@ayvid.ai",
    description:
      "Allocation enquiries, fund structure questions, and DDQ requests. Expect a response within one business day.",
  },
  {
    audience: "Y Combinator",
    email: "yc@ayvid.ai",
    description:
      "S26 batch correspondence and partner-of-the-week conversations. Same-day passphrase to the operations console for verified partners.",
  },
  {
    audience: "Press",
    email: "press@ayvid.ai",
    description:
      "Quote requests, fact-check enquiries, and embargoed comment. Please give us 48 hours.",
  },
  {
    audience: "Engineering candidates",
    email: "people@ayvid.ai",
    description:
      "We are hiring quietly. We particularly want infra engineers, ML researchers, and one experienced markets-microstructure engineer.",
  },
];

export default function ContactPage() {
  return (
    <>
      <section className="border-b border-app">
        <div className="container-page section-y reveal">
          <p className="eyebrow">Contact</p>
          <h1 className="font-display display-1 mt-6 max-w-[18ch] text-fg">
            By appointment.
          </h1>
          <p className="lede mt-7">
            We don&rsquo;t take inbound on chat widgets. The right thing is to
            email the right desk below; the right person will reply.
          </p>
        </div>
      </section>

      <section>
        <div className="container-page section-y">
          <ul className="grid grid-cols-1 gap-px overflow-hidden border border-app bg-[hsl(var(--border))] md:grid-cols-2">
            {CONTACTS.map((c) => (
              <li key={c.audience} className="bg-panel p-8">
                <p className="eyebrow text-[10px]">{c.audience}</p>
                <a
                  href={`mailto:${c.email}`}
                  className="mt-3 block font-display text-[28px] tracking-tight text-fg hover:text-brand"
                >
                  {c.email}
                </a>
                <p className="mt-3 max-w-md text-[13.5px] leading-relaxed text-muted">
                  {c.description}
                </p>
              </li>
            ))}
          </ul>
        </div>
      </section>

      <section className="border-t border-app bg-[hsl(var(--bg-elev))]">
        <div className="container-page section-y-sm grid grid-cols-1 items-end gap-10 md:grid-cols-12">
          <div className="md:col-span-7">
            <p className="eyebrow">Operations console</p>
            <h2 className="font-display display-3 mt-3 text-fg">
              Already been given the passphrase?
            </h2>
            <p className="prose-body mt-4 max-w-2xl">
              The internal control surface — equity curve, signal book, shock
              catalog, demo replay — is one click away.
            </p>
          </div>
          <div className="md:col-span-5 md:text-right">
            <Link
              href="/console"
              className="btn-primary"
            >
              Enter operations console
              <ArrowUpRight className="h-3.5 w-3.5" />
            </Link>
          </div>
        </div>
      </section>
    </>
  );
}
