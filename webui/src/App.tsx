import { useState } from "react";
import SessionList from "./components/SessionList";
import SessionDetail from "./components/SessionDetail";

export default function App() {
  const [selectedSession, setSelectedSession] = useState<string | null>(null);

  return (
    <div className="min-h-screen flex flex-col">
      {/* Header */}
      <header className="bg-white border-b border-gray-200 shadow-sm">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-4 flex items-center gap-3">
          <button
            onClick={() => setSelectedSession(null)}
            className="text-xl font-bold text-brand-700 hover:text-brand-500 transition-colors"
          >
            AgentTrace
          </button>
          <span className="text-gray-400 text-sm">token-waste profiler</span>
          {selectedSession && (
            <>
              <span className="text-gray-300">/</span>
              <span className="text-sm font-mono text-gray-600 truncate max-w-xs">
                {selectedSession}
              </span>
            </>
          )}
        </div>
      </header>

      {/* Main content */}
      <main className="flex-1 max-w-7xl mx-auto w-full px-4 sm:px-6 lg:px-8 py-6">
        {selectedSession == null ? (
          <SessionList onSelect={setSelectedSession} />
        ) : (
          <SessionDetail
            sessionId={selectedSession}
            onBack={() => setSelectedSession(null)}
          />
        )}
      </main>

      {/* Footer */}
      <footer className="border-t border-gray-200 bg-white py-3 text-center text-xs text-gray-400">
        AgentTrace — conservative waste attribution per{" "}
        <a
          href="https://github.com/kartha-vighnesh/agent-trace"
          className="underline hover:text-gray-600"
          target="_blank"
          rel="noreferrer"
        >
          methodology
        </a>
      </footer>
    </div>
  );
}
