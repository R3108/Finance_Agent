import type { Metadata } from "next";
import Link from "next/link";
import { RevealOnScroll, Spotlight } from "@/components/motion";
import Scene3D from "@/components/Scene3D";
import { DemoButton, Pricing } from "./LandingClient";

export const metadata: Metadata = {
  title: "Ledgerly — track every rupee automatically, with numbers you can trust",
  description:
    "Ledgerly reads your bank SMS, finds subscriptions and price hikes, tells you if you can afford it, splits bills over UPI, " +
    "and answers money questions in plain English — every figure calculated, never guessed.",
};

const FEATURES = [
  { icon: "✉", title: "Tracks itself from bank SMS",
    body: "Your phone forwards each UPI, card and salary alert as it arrives. No bank login, no screen-scraping, no CSV wrangling." },
  { icon: "?", title: "“Can I afford it?” before you buy",
    body: "Replays your next 90 days of paychecks and bills with the purchase in it — and tells you the date it would fit if not today." },
  { icon: "⇄", title: "Split & settle over UPI",
    body: "Split dinner or a trip, see who owes whom, and send a WhatsApp reminder with a one-tap UPI pay link." },
  { icon: "✓", title: "An assistant that can't make up numbers",
    body: "Ask anything in plain English. The AI picks the analysis; Python does the maths; every figure is checked before you see it." },
  { icon: "↻", title: "Finds money you're losing",
    body: "Price hikes, duplicate charges, forgotten trials and four streaming services you don't all need — each with the saving." },
  { icon: "⚿", title: "Bank-grade account security",
    body: "Two-factor sign-in, device management, and your data exportable or deletable in one click. Nothing is ever sold." },
];

const STEPS = [
  ["Start in a minute", "Sign up free, or open the live demo with 24 months of realistic data."],
  ["Bring your transactions", "Paste bank SMS, import a CSV, or let your phone forward alerts automatically."],
  ["Get answers, not spreadsheets", "Safe-to-spend, budgets, goals and alerts update on their own."],
];

const FAQ = [
  ["Do I have to give you my bank password?",
   "No. Ledgerly never asks for bank credentials. It works from the SMS alerts your bank already sends you, or a statement you export."],
  ["Can the AI get my numbers wrong?",
   "The AI never calculates. Every figure comes from deterministic code, and answers are checked against those results — anything that doesn't match is flagged, not shown as fact."],
  ["Is my data safe?",
   "Passwords are hashed, sessions are stored only as hashes, two-factor sign-in is built in, and you can download or permanently delete everything from Settings."],
  ["What happens after the free trial?",
   "You drop back to the Free plan automatically. No card is taken for the trial, so there's nothing to cancel."],
  ["Is this financial advice?",
   "No — Ledgerly gives educational guidance based on your own data. It isn't a registered investment adviser."],
];

function Brand() {
  return (
    <Link href="/welcome" className="brand" style={{ padding: 0 }}>
      <div className="brand-mark">L</div>
      <span>Ledgerly</span>
    </Link>
  );
}

/**
 * The product story around a 3D card: an SMS arrives, becomes a categorised row, and feeds an
 * affordability verdict. The chips float in sequence over the scene; without WebGL they sit on
 * the glow backdrop and read the same.
 */
function HeroVisual() {
  return (
    <div className="lp-stage" aria-hidden>
      <div className="lp-stage-glow" />
      <Scene3D variant="hero" />
      <div className="lp-chip lp-sms sms">
        <div className="small muted"><span className="lp-live" /> HDFCBK · now</div>
        Sent Rs.1,249.00 From HDFC Bank A/C *1234 To SWIGGY On 14/09/26…
      </div>
      <div className="card lp-chip lp-row txn">
        <div>
          <b>Swiggy</b>
          <div className="small muted">Dining · UPI · added automatically</div>
        </div>
        <b className="num">−₹1,249</b>
      </div>
      <div className="card verdict comfortable lp-mini lp-chip ok">
        <div className="mark">✓</div>
        <div>
          <b>Yes, you can afford the ₹18,000 trip</b>
          <div className="small muted">Balance stays above ₹82,000 for 90 days.</div>
        </div>
      </div>
    </div>
  );
}

export default function Welcome() {
  return (
    <div className="lp">
      <div className="lp-progress" aria-hidden />
      <div className="lp-aurora" aria-hidden><i /><i /><i /></div>
      <header className="lp-nav">
        <Brand />
        <nav aria-label="Site" className="row">
          <a href="#features" className="lp-navlink">Features</a>
          <a href="#pricing" className="lp-navlink">Pricing</a>
          <a href="#faq" className="lp-navlink">FAQ</a>
          <Link href="/login" className="btn">Sign in</Link>
          <Link href="/login?mode=signup" className="btn primary">Start free</Link>
        </nav>
      </header>

      <main>
        <section className="lp-hero">
          <div className="lp-hero-copy">
            <span className="badge accent">Built for UPI-first India</span>
            <h1>Every rupee tracked automatically. <span className="lp-grad">Every number you can trust.</span></h1>
            <p className="lp-lead">
              Ledgerly reads the alerts your bank already sends, shows where your money really goes, and answers
              “can I afford it?” before you tap pay — with maths done by code, not guessed by AI.
            </p>
            <div className="row wrap" style={{ gap: 12 }}>
              <Link href="/login?mode=signup" className="btn primary lg">Start free — no card</Link>
              <DemoButton />
            </div>
            <p className="small muted" style={{ marginTop: 12 }}>Free forever plan · 2-minute setup · delete your data any time</p>
          </div>
          <HeroVisual />
        </section>

        <section className="lp-trust" aria-label="Why people trust Ledgerly" data-reveal-group>
          <div><b>0</b><span>bank passwords asked for</span></div>
          <div><b>100%</b><span>of figures computed, not generated</span></div>
          <div><b>2FA</b><span>two-factor sign-in built in</span></div>
          <div><b>1-click</b><span>export or delete everything</span></div>
        </section>

        <section id="features" className="lp-section">
          <h2 className="lp-h2" data-reveal>Everything a money app should have done years ago</h2>
          <Spotlight>
            <div className="lp-features" data-reveal-group>
              {FEATURES.map((f) => (
                <div key={f.title} className="card lp-feature spot">
                  <div className="lp-icon" aria-hidden>{f.icon}</div>
                  <h3>{f.title}</h3>
                  <p className="muted">{f.body}</p>
                </div>
              ))}
            </div>
          </Spotlight>
        </section>

        <section className="lp-section">
          <h2 className="lp-h2" data-reveal>Up and running before your chai cools</h2>
          <ol className="lp-steps" data-reveal-group>
            {STEPS.map(([t, d], i) => (
              <li key={t} className="card">
                <span className="lp-step-n">{i + 1}</span>
                <h3>{t}</h3>
                <p className="muted">{d}</p>
              </li>
            ))}
          </ol>
        </section>

        <section id="pricing" className="lp-section" data-reveal>
          <h2 className="lp-h2">Simple pricing</h2>
          <p className="muted" style={{ textAlign: "center", marginBottom: 18 }}>Start free. Upgrade when automatic tracking earns its keep.</p>
          <Pricing />
        </section>

        <section id="faq" className="lp-section lp-faq" data-reveal-group>
          <h2 className="lp-h2">Questions people ask first</h2>
          {FAQ.map(([q, a]) => (
            <details key={q} className="card">
              <summary>{q}</summary>
              <p className="muted">{a}</p>
            </details>
          ))}
        </section>

        <section className="lp-final" data-reveal>
          <h2>See your money clearly in the next two minutes.</h2>
          <div className="row wrap" style={{ gap: 12, justifyContent: "center" }}>
            <Link href="/login?mode=signup" className="btn lg lp-invert">Create a free account</Link>
            <DemoButton className="btn lg lp-ghost" />
          </div>
        </section>
      </main>

      <footer className="lp-footer">
        <Brand />
        <span className="small muted">Educational guidance, not regulated financial advice.</span>
        <nav className="row small" aria-label="Footer">
          <Link href="/login">Sign in</Link>
          <a href="#pricing">Pricing</a>
          <a href="#faq">FAQ</a>
        </nav>
      </footer>
      <RevealOnScroll />
    </div>
  );
}
