export type Category =
  | "medical-device"
  | "ai-in-health"
  | "wellness-tech"
  | "fitness-tech"
  | "other";

export interface Article {
  id: string;
  url: string;
  title: string;
  source: string;
  published_at: string;
  category: Category | string;
  country: string | null;
  summary: string;
  ingested_at: string;
}

export interface ArticlesFile {
  updated_at: string | null;
  articles: Article[];
}

export const CATEGORY_LABELS: Record<string, string> = {
  "medical-device": "Medical devices",
  "ai-in-health": "AI in health",
  "wellness-tech": "Wellness tech",
  "fitness-tech": "Fitness tech",
  other: "Other",
};

/** Global + India + top 5 digital-health markets */
export const FEATURED_REGIONS = [
  { code: "GLOBAL", label: "Global" },
  { code: "IN", label: "India" },
  { code: "US", label: "United States" },
  { code: "CN", label: "China" },
  { code: "GB", label: "United Kingdom" },
  { code: "DE", label: "Germany" },
  { code: "JP", label: "Japan" },
] as const;

export const COUNTRY_LABELS: Record<string, string> = Object.fromEntries(
  FEATURED_REGIONS.map((r) => [r.code, r.label]),
);
