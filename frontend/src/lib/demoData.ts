/**
 * Deterministic demo data driving the live-shock visualisation.
 *
 * Mirrors a real (Suez Canal 2021-style) supply-chain shock cascading
 * through Neo4j topology into the F&O signal generator. Used purely for
 * the YC application demo video. When AYVID_API_BASE is configured, the
 * page can subscribe to /sse/shocks and replace this fixture in real-time.
 */

export type ShockEvent = {
  id: string;
  detectedAt: string;
  source: string;
  headline: string;
  category:
    | "supply_disruption"
    | "tariff_escalation"
    | "rate_decision"
    | "geopolitical"
    | "earnings"
    | "macro_print";
  magnitude: number;
  confidence: number;
  causalChain: string;
};

export type GraphNode = {
  id: string;
  label: string;
  ring: 0 | 1 | 2 | 3 | 4;
  classification: "shock" | "beneficiary" | "victim" | "neutral";
  delta: number;
};

export type GraphEdge = {
  id: string;
  from: string;
  to: string;
  weight: number;
  hop: 1 | 2 | 3 | 4;
  label: string;
};

export type Signal = {
  id: string;
  emittedAt: string;
  ticker: string;
  instrument: "EQUITY" | "INDEX_PUT" | "INDEX_CALL" | "INDEX_SPREAD" | "INDEX_FUTURE" | "FX" | "COMMODITY_FUTURE" | "CRYPTO" | "ETF" | "RATE_FUTURE";
  direction: "LONG" | "SHORT";
  strategy: string;
  conviction: number;
  expectedR: number;
  rationale: string;
  graphIntel: boolean;
};

export type Trade = {
  id: string;
  filledAt: string;
  ticker: string;
  side: "BUY" | "SELL";
  qty: number;
  price: number;
  notional: number;
  status: "FILLED" | "WORKING" | "REJECTED";
  postCostPnl?: number;
};

export const demoShock: ShockEvent = {
  id: "shock_2026_04_19_red_sea",
  detectedAt: "2026-04-19T07:42:18Z",
  source: "EDGAR 8-K + Reuters wire (3 corroborating sources)",
  headline:
    "Red Sea tanker re-routing accelerates: 38% of Asia-EU container traffic now via Cape of Good Hope",
  category: "supply_disruption",
  magnitude: -0.071,
  confidence: 0.86,
  causalChain:
    "Red Sea closure → 12-day rerouting cost → freight rates +47% → tanker beneficiaries (MAERSK, ZIM, FRO) → European retail/auto inventory delays → bearish ^STOXX50E",
};

export const demoGraph: { nodes: GraphNode[]; edges: GraphEdge[] } = {
  nodes: [
    { id: "shock", label: "Red Sea Closure", ring: 0, classification: "shock", delta: -0.071 },
    { id: "MAERSK-B.CO", label: "MAERSK", ring: 1, classification: "beneficiary", delta: 0.063 },
    { id: "ZIM", label: "ZIM", ring: 1, classification: "beneficiary", delta: 0.081 },
    { id: "FRO", label: "FRO", ring: 1, classification: "beneficiary", delta: 0.054 },
    { id: "BP", label: "BP", ring: 2, classification: "beneficiary", delta: 0.037 },
    { id: "SHEL", label: "SHEL", ring: 2, classification: "beneficiary", delta: 0.041 },
    { id: "VOLV-B.ST", label: "Volvo", ring: 2, classification: "victim", delta: -0.029 },
    { id: "BMW.DE", label: "BMW", ring: 2, classification: "victim", delta: -0.024 },
    { id: "^STOXX50E", label: "STOXX 50", ring: 3, classification: "victim", delta: -0.018 },
    { id: "^FTSE", label: "FTSE 100", ring: 3, classification: "victim", delta: -0.011 },
  ],
  edges: [
    { id: "e1", from: "shock", to: "MAERSK-B.CO", weight: 0.92, hop: 1, label: "freight rates +47%" },
    { id: "e2", from: "shock", to: "ZIM", weight: 0.89, hop: 1, label: "containership demand" },
    { id: "e3", from: "shock", to: "FRO", weight: 0.78, hop: 1, label: "tanker rerouting" },
    { id: "e4", from: "MAERSK-B.CO", to: "BP", weight: 0.61, hop: 2, label: "bunker fuel demand" },
    { id: "e5", from: "FRO", to: "SHEL", weight: 0.58, hop: 2, label: "longer voyages" },
    { id: "e6", from: "MAERSK-B.CO", to: "VOLV-B.ST", weight: 0.49, hop: 2, label: "delayed parts" },
    { id: "e7", from: "ZIM", to: "BMW.DE", weight: 0.46, hop: 2, label: "EU auto inventory" },
    { id: "e8", from: "VOLV-B.ST", to: "^STOXX50E", weight: 0.41, hop: 3, label: "EU industrials weight" },
    { id: "e9", from: "BMW.DE", to: "^STOXX50E", weight: 0.39, hop: 3, label: "DAX feed-through" },
    { id: "e10", from: "BP", to: "^FTSE", weight: 0.36, hop: 3, label: "FTSE energy weight" },
  ],
};

export const demoSignals: Signal[] = [
  {
    id: "sig_001",
    emittedAt: "2026-04-19T07:42:24Z",
    ticker: "ZIM",
    instrument: "EQUITY",
    direction: "LONG",
    strategy: "supply_chain_beneficiary",
    conviction: 0.82,
    expectedR: 2.4,
    rationale:
      "Tier-1 beneficiary of Red Sea closure (vol_r=2.1, factor=0.62, mag=+8.1%)",
    graphIntel: true,
  },
  {
    id: "sig_002",
    emittedAt: "2026-04-19T07:42:25Z",
    ticker: "MAERSK-B.CO",
    instrument: "EQUITY",
    direction: "LONG",
    strategy: "supply_chain_beneficiary",
    conviction: 0.79,
    expectedR: 2.1,
    rationale: "Container rates regression model: +47% → +6.3% MAERSK target",
    graphIntel: true,
  },
  {
    id: "sig_003",
    emittedAt: "2026-04-19T07:42:27Z",
    ticker: "^STOXX50E",
    instrument: "INDEX_PUT",
    direction: "LONG",
    strategy: "shock_directed_put_fesx",
    conviction: 0.73,
    expectedR: 1.8,
    rationale:
      "3-hop bearish bias on STOXX 50 via VOLV/BMW supply-chain feed-through (8d weekly)",
    graphIntel: true,
  },
  {
    id: "sig_004",
    emittedAt: "2026-04-19T07:42:28Z",
    ticker: "BP",
    instrument: "EQUITY",
    direction: "LONG",
    strategy: "supply_chain_beneficiary",
    conviction: 0.66,
    expectedR: 1.7,
    rationale: "2-hop beneficiary via bunker-fuel demand from longer voyages",
    graphIntel: true,
  },
];

export const demoTrades: Trade[] = [
  {
    id: "trd_001",
    filledAt: "2026-04-19T07:42:31Z",
    ticker: "ZIM",
    side: "BUY",
    qty: 1840,
    price: 22.41,
    notional: 41_234.4,
    status: "FILLED",
    postCostPnl: +118.4,
  },
  {
    id: "trd_002",
    filledAt: "2026-04-19T07:42:33Z",
    ticker: "MAERSK-B.CO",
    side: "BUY",
    qty: 240,
    price: 12_180,
    notional: 2_923_200,
    status: "FILLED",
  },
  {
    id: "trd_003",
    filledAt: "2026-04-19T07:42:36Z",
    ticker: "^STOXX50E 4970 PE",
    side: "BUY",
    qty: 12,
    price: 18.4,
    notional: 22_080,
    status: "FILLED",
  },
  {
    id: "trd_004",
    filledAt: "2026-04-19T07:42:39Z",
    ticker: "BP",
    side: "BUY",
    qty: 5_400,
    price: 4.93,
    notional: 26_622,
    status: "WORKING",
  },
];
