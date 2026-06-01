import type { WasteReport } from "../api";

interface Props {
  waste: WasteReport;
}

function StatCard({
  label,
  value,
  sub,
  highlight,
}: {
  label: string;
  value: string;
  sub?: string;
  highlight?: boolean;
}) {
  return (
    <div
      className={`rounded-lg border p-4 ${highlight ? "border-red-200 bg-red-50" : "border-gray-200 bg-white"}`}
    >
      <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">
        {label}
      </p>
      <p
        className={`mt-1 text-2xl font-bold ${highlight ? "text-red-700" : "text-gray-900"}`}
      >
        {value}
      </p>
      {sub && <p className="mt-0.5 text-xs text-gray-400">{sub}</p>}
    </div>
  );
}

function fmt$(n: number): string {
  if (n < 0.001) return `$${(n * 1000).toFixed(4)}m`;
  return `$${n.toFixed(4)}`;
}

export default function CostPanel({ waste }: Props) {
  const wasteHighlight = waste.avoidable_pct > 10;

  return (
    <div>
      <h2 className="text-lg font-semibold text-gray-900 mb-3">
        Cost Summary
      </h2>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <StatCard
          label="Avoidable waste"
          value={`${waste.avoidable_pct.toFixed(1)}%`}
          sub="of billed input cost"
          highlight={wasteHighlight}
        />
        <StatCard
          label="Total billed input"
          value={fmt$(waste.total_billed_input_cost)}
        />
        <StatCard
          label="Wasted (re-reads)"
          value={fmt$(waste.wasted_cost)}
          highlight={wasteHighlight}
        />
        <StatCard
          label="Fixed overhead"
          value={fmt$(waste.fixed_overhead_cost)}
          sub="system prompt + tools"
        />
      </div>
      <div className="mt-3 grid grid-cols-2 gap-3">
        <StatCard
          label="Total requests"
          value={String(waste.total_requests)}
        />
        <StatCard
          label="Files re-read"
          value={String(waste.wasted_requests)}
          highlight={waste.wasted_requests > 0}
        />
      </div>

      {waste.avoidable_pct > 0 && (
        <div className="mt-4 rounded-md bg-amber-50 border border-amber-200 px-4 py-3 text-sm text-amber-800">
          <strong>{waste.avoidable_pct.toFixed(1)}%</strong> of billed input
          tokens were spent re-reading identical file content. A codebase index
          or read cache could eliminate this cost.
        </div>
      )}

      <p className="mt-3 text-xs text-gray-400">
        Methodology: waste = Σ(re-read identical content) × cache-read rate.
        Conservative — only provably identical re-sent content counted.
      </p>
    </div>
  );
}
