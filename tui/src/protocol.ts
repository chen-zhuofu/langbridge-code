/** JSONL protocol shared with the Python bridge (langbridge_code/ui/bridge.py). */

export interface SessionItem {
  path: string;
  label: string;
}

export interface ModelItem {
  id: string;
  provider: string;
}

export type EngineEvent =
  | {
      type: "hello";
      model: string;
      version: string;
      cwd: string;
      git_branch: string;
      sessions: SessionItem[];
    }
  | { type: "system"; text: string; style?: string }
  | { type: "assistant"; text: string }
  | { type: "queued"; text: string; count: number }
  | { type: "turn_started"; text: string }
  | { type: "trace"; role: string; kind: string; text: string; tool?: string }
  | { type: "stream"; role: string; kind: string; text: string }
  | {
      type: "state";
      state: string;
      workflow: string;
      turn_active: boolean;
      yolo: boolean;
      queued: number;
      goal_active: boolean;
    }
  | { type: "context_line"; text: string }
  | { type: "approval_request"; summary: string; details: string }
  | { type: "approval_resolved"; approved: boolean }
  | { type: "question"; text: string; options: string[] }
  | { type: "answer_recorded"; text: string }
  | { type: "turn_end"; status: "ok" | "stopped" | "error"; message?: string }
  | { type: "sessions"; items: SessionItem[] }
  | { type: "session_new" }
  | {
      type: "session_resumed";
      label: string;
      preview: string;
      conversation?: { role: string; text: string }[];
    }
  | { type: "queue"; items: string[] }
  | { type: "models"; items: ModelItem[]; current: string; provider?: string }
  | { type: "model"; model: string; provider?: string };

export type ClientMessage =
  | { type: "user_message"; text: string }
  | { type: "approval"; approved: boolean }
  | { type: "answer"; text: string }
  | { type: "yolo"; value: boolean }
  | { type: "pause_toggle" }
  | { type: "stop" }
  | { type: "new_session" }
  | { type: "list_sessions" }
  | { type: "resume_session"; path: string }
  | { type: "delete_session"; path: string }
  | { type: "goal"; text: string }
  | { type: "queue_list" }
  | { type: "queue_clear" }
  | { type: "list_models" }
  | { type: "set_model"; model: string; provider?: string }
  | { type: "quit" };
