import type { TurnRank } from "../api";

interface Props {
  turns: TurnRank[];
}

const CAUSE_STYLES: Record<string, { badge: string; label: string }> = {
  "re-read": {
    badge: "bg-red-100 text-red-800 border border-red-200",
    label: "Re-read",
  },
  "context-bloat": {
    badge: "bg-orange-100 text-orange-800 border border-orange-200",
    label: "Context bloat",
  },
  "large-tool-output": {
    badge: "bg-yellow-100 text-yellow-800 border border-yellow-200",
    label: "Large output",
  },
  scan: {
    badge: "bg-blue-100 text-blue-800 border border-blue-200",
    label: "Scan",
  },
  normal: {
    badge: "bg-gray-100 text-gray-600 border border-gray-200",
    label: "Normal",
  },
};

function fmtTokens(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

function fmtCost(n: number): string {
  if (n < 0.0001) return `$${(n * 1_000_000).toFixed(2)}µ`;
  if (n < 0.001) return `$${(n * 1000).toFixed(4)}m`;
  return `$${n.toFixed(5)}`;
}

function fmtTs(ts: string): string {
  try {
    return new Date(ts).toLocaleTimeString();
  } catch {
    return ts;
  }
}

export default function TurnList({ turns }: Props) {
  if (turns.length === 0) {
    return (
      <div className="text-center py-8 text-gray-400 text-sm">
        No turns recorded for this session.
      </div>
    );
  }

  return (
    <div>
      <h2 className="text-lg font-semibold text-gray-900 mb-3">
        Top Turns by Cost
      </h2>
      <div className="overflow-hidden rounded-lg border border-gray-200 shadow-sm">
        <table className="min-w-full divide-y divide-gray-200 text-sm">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">#</th>
              <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">Time</th>
              <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">Model</th>
              <th className="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase">In</th>
              <th className="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase">Out</th>
              <th className="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase">Cached</th>
              <th className="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase">Cost</th>
              <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">Cause</th>
            </tr>
          </thead>
          <tbody className="bg-white divide-y divide-gray-100">
            {turns.map((t) => {
              const causeStyle =
                CAUSE_STYLES[t.cause_label] ?? CAUSE_STYLES.normal;
              return (
                <tr key={t.request_id} className="hover:bg-gray-50">
                  <td className="px-3 py-2 text-gray-400 font-mono">{t.rank}</td>
                  <td className="px-3 py-2 text-gray-600">{fmtTs(t.recv_ts)}</td>
                  <td className="px-3 py-2 text-gray-700 font-mono text-xs max-w-[120px] truncate">
                    {t.model ?? "—"}
                  </td>
                  <td className="px-3 py-2 text-right text-gray-700">
                    {fmtTokens(t.input_tokens)}
                  </td>
                  <td className="px-3 py-2 text-right text-gray-700">
                    {fmtTokens(t.output_tokens)}
                  </td>
                  <td className="px-3 py-2 text-right text-amber-700">
                    {fmtTokens(t.cache_read_tokens)}
                  </td>
                  <td className="px-3 py-2 text-right font-mono font-medium text-gray-900">
                    {fmtCost(t.total_cost)}
                  </td>
                  <td className="px-3 py-2">
                    <span
                      className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${causeStyle.badge}`}
                    >
                      {causeStyle.label}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-xs text-gray-400">
        Cause labels are heuristics for triage. Re-read = identical file
        content seen earlier in session.
      </p>
    </div>
  );
}
