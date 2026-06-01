import { useEffect, useState } from "react";
import { api, ContextGrowthPoint, TurnRank, WasteReport } from "../api";
import CostPanel from "./CostPanel";
import ContextGrowthChart from "./ContextGrowthChart";
import TurnList from "./TurnList";

interface Props {
  sessionId: string;
  onBack: () => void;
}

interface SessionData {
  waste: WasteReport;
  turns: TurnRank[];
  growth: ContextGrowthPoint[];
}

export default function SessionDetail({ sessionId, onBack }: Props) {
  const [data, setData] = useState<SessionData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const ctrl = new AbortController();

    Promise.all([
      api.getSession(sessionId),
      api.getTurns(sessionId, 50),
      api.getGrowth(sessionId),
    ])
      .then(([waste, turns, growth]) => {
        if (!ctrl.signal.aborted) {
          setData({ waste, turns, growth });
        }
      })
      .catch((e: unknown) => {
        if (!ctrl.signal.aborted) {
          setError(String(e));
        }
      })
      .finally(() => {
        if (!ctrl.signal.aborted) {
          setLoading(false);
        }
      });

    return () => ctrl.abort();
  }, [sessionId]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64 text-gray-500">
        Loading session…
      </div>
    );
  }

  if (error || data == null) {
    return (
      <div className="rounded-md bg-red-50 border border-red-200 p-4 text-red-700">
        {error ?? "Unknown error"}
      </div>
    );
  }

  return (
    <div className="space-y-8">
      {/* Back button */}
      <button
        onClick={onBack}
        className="flex items-center gap-1 text-sm text-gray-500 hover:text-gray-800 transition-colors"
      >
        ← All sessions
      </button>

      {/* Cost summary */}
      <section>
        <CostPanel waste={data.waste} />
      </section>

      {/* Context growth chart */}
      <section className="bg-white rounded-lg border border-gray-200 shadow-sm p-5">
        <ContextGrowthChart data={data.growth} />
      </section>

      {/* Turn list */}
      <section>
        <TurnList turns={data.turns} />
      </section>
    </div>
  );
}
