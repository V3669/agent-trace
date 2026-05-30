CREATE TABLE IF NOT EXISTS requests (
  id INTEGER PRIMARY KEY,
  recv_ts TEXT NOT NULL,
  conn_id TEXT NOT NULL,
  agent_id TEXT NOT NULL,
  provider TEXT NOT NULL,
  model TEXT,
  system_prompt_hash TEXT,
  message_hashes_json TEXT,
  tool_defs_hash TEXT,
  body_blob BLOB,
  usage_input INTEGER,
  usage_output INTEGER,
  cache_read_input INTEGER,
  cache_creation_input INTEGER,
  usage_source TEXT,
  estimate_confidence TEXT
);

CREATE TABLE IF NOT EXISTS file_read_events (
  id INTEGER PRIMARY KEY,
  request_id INTEGER NOT NULL REFERENCES requests(id),
  path TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  approx_tokens INTEGER
);

CREATE TABLE IF NOT EXISTS session_events (
  id INTEGER PRIMARY KEY,
  session_id TEXT NOT NULL,
  request_id INTEGER NOT NULL REFERENCES requests(id),
  event_type TEXT,
  parent_session_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_req_conn ON requests(conn_id, recv_ts);
CREATE INDEX IF NOT EXISTS idx_fre_hash ON file_read_events(path, content_hash);
CREATE INDEX IF NOT EXISTS idx_session ON session_events(session_id, request_id);
