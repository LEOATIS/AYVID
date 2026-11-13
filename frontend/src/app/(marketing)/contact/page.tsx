import Link from "next/link";
import { ArrowUpRight } from "lucide-react";

export const metadata = {
  title: "Contact",
  description: "Talk to the founder, request access to the AYVID workspace, or join the team.",
};

const CONTACTS: { audience: string; email: string; description: string }[] = [
  {
    audience: "Founder",
    email: "likhith@ayvid.ai",
    description: "Questions about the product, a demo, or a research problem you want AYVID to handle. I read every email.",
  },
  {
    audience: "Investors & Y Combinator",
    email: "yc@ayvid.ai",
    description: "Partner and investor conversations. Ask for access and you'll have the workspace password the same day.",
  },
  {
    audience: "Join us",
    email: "people@ayvid.ai",
    description: "We're looking for a quantitative / ML research co-founder and engineers who care about getting numbers right.",
  },
];

export default function ContactPage() {
  return (
    <>
      <section className="border-b border-app">
        <div className="container-page section-y reveal">
          <p className="eyebrow">Contact</p>
          <h1 className="font-display display-1 mt-6 max-w-[18ch] text-fg">Talk to the person who built it.</h1>
          <p className="lede mt-7">
            AYVID is founder-led. Email directly and you&rsquo;ll get a reply from me, usually the same day.
          </p>
        </div>
      </section>

      <section>
        <div className="container-page section-y">
          <ul className="grid grid-cols-1 gap-px overflow-hidden border border-app bg-[hsl(var(--border))] md:grid-cols-3">
            {CONTACTS.map((c) => (
              <li key={c.audience} className="bg-panel p-8">
                <p className="eyebrow text-[10px]">{c.audience}</p>
                <a href={`mailto:${c.email}`} className="mt-3 block break-all font-display text-[24px] tracking-tight text-fg hover:text-brand">
                  {c.email}
                </a>
                <p className="mt-3 max-w-md text-[13.5px] leading-relaxed text-muted">{c.description}</p>
              </li>
            ))}
          </ul>
          <p className="mt-6 text-[14px] text-muted">
            Code:{" "}
            <a href="https://github.com/LEOATIS/AYVID" className="text-fg underline" rel="noopener noreferrer" target="_blank">
              github.com/LEOATIS/AYVID
            </a>
          </p>
        </div>
      </section>

      <section className="border-t border-app bg-[hsl(var(--bg-elev))]">
        <div className="container-page section-y-sm grid grid-cols-1 items-end gap-10 md:grid-cols-12">
          <div className="md:col-span-7">
            <p className="eyebrow">Already have access?</p>
            <h2 className="font-display display-3 mt-3 text-fg">Go straight in.</h2>
            <p className="prose-body mt-4 max-w-2xl">
              The research workspace and the Shock Lab are open to internal users and invited partners. You&rsquo;ll be
              asked for the access password once.
            </p>
          </div>
          <div className="flex flex-wrap gap-2 md:col-span-5 md:justify-end">
            <Link href="/research" className="btn-primary">
              Research workspace <ArrowUpRight className="h-3.5 w-3.5" aria-hidden />
            </Link>
            <Link href="/console/live-shock?demo=1" className="btn-ghost">
              Shock Lab <ArrowUpRight className="h-3.5 w-3.5" aria-hidden />
            </Link>
          </div>
        </div>
      </section>
    </>
  );
}
