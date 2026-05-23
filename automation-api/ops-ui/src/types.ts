export type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue };

export type JsonRecord = Record<string, any>;

export type ViewId = 'builder' | 'tasks' | 'runs' | 'reviews' | 'sources' | 'audit' | 'system';

export type OpsBootstrap = {
  generated_at: string;
  app: {
    name: string;
    surface: string;
    legacy_url: string;
    api_base: string;
  };
  navigation: Array<{ id: ViewId; label: string; priority: string }>;
  features: {
    sse: boolean;
    polling_fallback_seconds: number;
    events_url: string;
    min_page_size: number;
    default_page_size: number;
    max_page_size: number;
  };
  security: {
    admin_keys_exposed: boolean;
    approval_nonces_persisted: boolean;
    action_headers_required: boolean;
    openapi_exposed: boolean;
  };
};

export type ActionHeaderKind =
  | 'approval'
  | 'run'
  | 'taskState'
  | 'taskArchive'
  | 'versionRevert'
  | 'taskChange'
  | 'capabilityProposal'
  | 'capabilityImplementation'
  | 'capabilityGap'
  | 'sourceProposal';
