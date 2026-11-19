-- The relay's whole memory. Every body is ciphertext only the PC and its phones can open.
--   npx wrangler d1 execute vector-relay --remote --file schema.sql
CREATE TABLE IF NOT EXISTS channels (
  name  TEXT PRIMARY KEY,
  owner TEXT NOT NULL,      -- SHA-256 of the PC's write token
  state TEXT,
  seen  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS asks (
  channel TEXT NOT NULL,
  id      TEXT NOT NULL,
  body    TEXT NOT NULL,
  t       INTEGER NOT NULL,
  PRIMARY KEY (channel, id)
);
CREATE TABLE IF NOT EXISTS answers (
  channel TEXT NOT NULL,
  id      TEXT NOT NULL,
  body    TEXT NOT NULL,
  t       INTEGER NOT NULL,
  PRIMARY KEY (channel, id)
);
