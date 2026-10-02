export const SERVER_URL = "http://127.0.0.1:7860";
export const DEFAULT_PRESET = "natural";

// Same values as DEFAULTS in static/index.html; ranges match Settings in server.py.
export const DEFAULT_SETTINGS = {
  saturation: 1, warmth: 0, hue: 0, clean_whites: 0.5, size: 576, denoise: 25, skip_colored: true,
};
export const SETTING_RANGES = {
  saturation: [0, 2.5],
  warmth: [-1, 1],
  hue: [-180, 180],
  clean_whites: [0, 1],
  size: [256, 1024],
  denoise: [0, 75],
};
export const SIZES = [
  { value: 576, label: "Balanced (576)" },
  { value: 768, label: "More detail (768)" },
  { value: 1024, label: "Max (1024, slower)" },
];

// Same values as PRESETS in static/index.html; keep the two in sync.
export const PRESETS = {
  natural: { label: "Natural", tone: { saturation: 1, warmth: 0, hue: 0, clean_whites: 0.5 } },
  vivid: { label: "Vivid", tone: { saturation: 1.6, warmth: 0.05, hue: 0, clean_whites: 0.7 } },
  soft: { label: "Soft", tone: { saturation: 0.7, warmth: 0.1, hue: 0, clean_whites: 0.6 } },
  warm: { label: "Warm vintage", tone: { saturation: 0.9, warmth: 0.55, hue: 0, clean_whites: 0.2 } },
  cool: { label: "Cool", tone: { saturation: 1.1, warmth: -0.4, hue: 0, clean_whites: 0.6 } },
};
