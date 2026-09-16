# Context corpus

Do not place a shared global chat history here during an experiment.  Each
multi-turn run receives an isolated runtime context under its own output
directory.  Static context fixtures, if needed for a frozen benchmark, must be
JSONL records with `knowledge_type: "context"`, `conversation_id`, `turn_id`,
`user_request`, `scene_plan_snapshot`, and `change_summary`.

