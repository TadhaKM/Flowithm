// Shared step indicator for the /onboarding/* pages and /setup.
// Render at the top of each page. Active step gets the teal accent;
// completed steps get a faint check; future steps stay zinc.

type Step = "setup" | "connect" | "generate";

const ORDER: Step[] = ["setup", "connect", "generate"];
const LABELS: Record<Step, string> = {
  setup: "Setup",
  connect: "Connect",
  generate: "Generate",
};

export default function StepIndicator({ active }: { active: Step }) {
  const activeIdx = ORDER.indexOf(active);
  return (
    <div className="mb-10 flex items-center justify-center gap-2 text-xs">
      {ORDER.map((step, i) => {
        const state = i < activeIdx ? "done" : i === activeIdx ? "active" : "future";
        return (
          <div key={step} className="flex items-center gap-2">
            <span
              className={`flex h-6 w-6 items-center justify-center rounded-full border text-[11px] font-medium tabular-nums ${
                state === "active"
                  ? "border-[#1D9E75] bg-[#1D9E75] text-white"
                  : state === "done"
                    ? "border-[#1D9E75]/40 bg-[#1D9E75]/10 text-[#1D9E75]"
                    : "border-zinc-700 text-zinc-500"
              }`}
            >
              {state === "done" ? "✓" : i + 1}
            </span>
            <span
              className={`uppercase tracking-wider ${
                state === "active"
                  ? "text-zinc-100"
                  : state === "done"
                    ? "text-[#1D9E75]"
                    : "text-zinc-600"
              }`}
            >
              {LABELS[step]}
            </span>
            {i < ORDER.length - 1 && (
              <span
                className={`mx-1 h-px w-8 ${
                  state === "future" ? "bg-zinc-800" : "bg-[#1D9E75]/40"
                }`}
              />
            )}
          </div>
        );
      })}
    </div>
  );
}
