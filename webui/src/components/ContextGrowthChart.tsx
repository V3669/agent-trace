import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { ContextGrowthPoint } from "../api";

interface Props {
  data: ContextGrowthPoint[];
}

interface ChartRow {
  idx: number;
  input: number;
  cached: number;
  plain: number;
  cumCost: number;
}

function buildRows(data: ContextGrowthPoint[]): ChartRow[] {
  return data.map((p) => ({
    idx: p.request_index,
    input: p.input_tokens,
    cached: p.cache_read_tokens,
    plain: p.plain_input_tokens,
    cumCost: parseFloat(p.cumulative_billed_cost.toFixed(6)),
  }));
}

export default function ContextGrowthChart({ data }: Props) {
  if (data.length === 0) {
    return (
      <div className="flex items-center justify-center h-40 text-gray-400 text-sm">
        No data for this session.
      </div>
    );
  }

  const rows = buildRows(data);

  return (
    <div>
      <h2 className="text-lg font-semibold text-gray-900 mb-3">
        Context Growth
      </h2>
      <p className="text-sm text-gray-500 mb-4">
        Input tokens per request — cached vs. plain. Large cached fraction
        means old context is being resent.
      </p>
      <ResponsiveContainer width="100%" height={260}>
        <LineChart data={rows} margin={{ top: 4, right: 12, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
          <XAxis
            dataKey="idx"
            label={{ value: "Request #", position: "insideBottom", offset: -4 }}
            tick={{ fontSize: 11 }}
          />
          <YAxis
            yAxisId="tokens"
            tick={{ fontSize: 11 }}
            label={{
              value: "Tokens",
              angle: -90,
              position: "insideLeft",
              style: { fontSize: 11 },
            }}
          />
          <YAxis
            yAxisId="cost"
            orientation="right"
            tick={{ fontSize: 11 }}
            tickFormatter={(v: number) => `$${v.toFixed(4)}`}
          />
          <Tooltip
            formatter={(value: number, name: string) => {
              if (name === "cumCost") return [`$${value.toFixed(6)}`, "Cumulative cost"];
              return [value.toLocaleString(), name];
            }}
          />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Line
            yAxisId="tokens"
            type="monotone"
            dataKey="input"
            name="Total input"
            stroke="#6366f1"
            dot={false}
            strokeWidth={2}
          />
          <Line
            yAxisId="tokens"
            type="monotone"
            dataKey="cached"
            name="Cached (read)"
            stroke="#f59e0b"
            dot={false}
            strokeWidth={2}
            strokeDasharray="5 3"
          />
          <Line
            yAxisId="tokens"
            type="monotone"
            dataKey="plain"
            name="Plain input"
            stroke="#10b981"
            dot={false}
            strokeWidth={1}
          />
          <Line
            yAxisId="cost"
            type="monotone"
            dataKey="cumCost"
            name="cumCost"
            stroke="#ef4444"
            dot={false}
            strokeWidth={1.5}
            strokeDasharray="3 2"
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
