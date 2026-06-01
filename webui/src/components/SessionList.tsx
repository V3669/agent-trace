import { useEffect, useState } from "react";
import { api, SessionSummary } from "../api";

interface Props {
  onSelect: (sessionId: string) => void;
}

const AGENT_BADGE: Record<string, string> = {
  claude_code: "bg-violet-100 text-violet-800",
  aider: "bg-green-100 text-green-800",
  unknown: "bg-gray-100 text-gray-600",
};

const PROVIDER_BADGE: Record<string, string> = {
  anthropic: "bg-orange-100 text-orange-800",
  openai: "bg-blue-100 text-blue-800",
  unknown: "bg-gray-100 text-gray-600",
};

function fmtTs(ts: string): string {
  try {
    return new Date(ts).toLocaleString();
  } catch {
    return ts;
  }
}

export default function SessionList({ onSelect }: Props) {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listSessions()
      .then(setSessions)
      .catch((e: unknown) => setError(String(e)))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64 text-gray-500">
        Loading sessions…
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-md bg-red-50 border border-red-200 p-4 text-red-700">
        {error}
      </div>
    );
  }

  if (sessions.length === 0) {
    return (
      <div className="text-center py-16 text-gray-400">
        <p className="text-lg font-medium">No sessions yet</p>
        <p className="mt-2 text-sm">
          Run <code className="bg-gray-100 px-1 rounded">agenttrace start</code>{" "}
          and use Claude Code or Aider to record your first session.
        </p>
      </div>
    );
  }

  return (
    <div>
      <h1 className="text-2xl font-semibold text-gray-900 mb-4">Sessions</h1>
      <div className="overflow-hidden rounded-lg border border-gray-200 shadow-sm">
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                Session ID
              </th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                Started
              </th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                Requests
              </th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                Agent
              </th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                Provider
              </th>
            </tr>
          </thead>
          <tbody className="bg-white divide-y divide-gray-100">
            {sessions.map((s) => (
              <tr
                key={s.session_id}
                onClick={() => onSelect(s.session_id)}
                className="hover:bg-brand-50 cursor-pointer transition-colors"
              >
                <td className="px-4 py-3 font-mono text-xs text-gray-700">
                  {s.session_id}
                </td>
                <td className="px-4 py-3 text-sm text-gray-600">
                  {fmtTs(s.start_ts)}
                </td>
                <td className="px-4 py-3 text-sm text-gray-700 text-right pr-8">
                  {s.total_requests}
                </td>
                <td className="px-4 py-3">
                  <span
                    className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${AGENT_BADGE[s.agent_id] ?? AGENT_BADGE.unknown}`}
                  >
                    {s.agent_id}
                  </span>
                </td>
                <td className="px-4 py-3">
                  <span
                    className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${PROVIDER_BADGE[s.provider] ?? PROVIDER_BADGE.unknown}`}
                  >
                    {s.provider}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
