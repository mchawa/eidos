/**
 * Material Design 3 (MD3) Design Tokens Bridge for Eidos Living Portal.
 * Synthesizes W3C DTCG tokens from eidos/design/systems/core/tokens.json
 * into MD3 Tonal Color Roles, Surface Container Elevations, and State Layers.
 */

export interface MD3ColorSystem {
  primary: string;
  onPrimary: string;
  primaryContainer: string;
  onPrimaryContainer: string;
  secondary: string;
  onSecondary: string;
  secondaryContainer: string;
  onSecondaryContainer: string;
  tertiary: string;
  onTertiary: string;
  tertiaryContainer: string;
  onTertiaryContainer: string;
  surface: string;
  onSurface: string;
  surfaceDim: string;
  surfaceBright: string;
  surfaceContainerLowest: string;
  surfaceContainerLow: string;
  surfaceContainer: string;
  surfaceContainerHigh: string;
  surfaceContainerHighest: string;
  outline: string;
  outlineVariant: string;
  inverseSurface: string;
  inverseOnSurface: string;
  inversePrimary: string;
  scrim: string;
  shadow: string;
}

export const md3DarkColors: MD3ColorSystem = {
  primary: "#60a5fa",
  onPrimary: "#082f49",
  primaryContainer: "rgba(59, 130, 246, 0.18)",
  onPrimaryContainer: "#bfdbfe",
  secondary: "#a78bfa",
  onSecondary: "#2e1065",
  secondaryContainer: "rgba(139, 92, 246, 0.18)",
  onSecondaryContainer: "#ddd6fe",
  tertiary: "#34d399",
  onTertiary: "#022c22",
  tertiaryContainer: "rgba(16, 185, 129, 0.18)",
  onTertiaryContainer: "#a7f3d0",
  surface: "#090d16",
  onSurface: "#f8fafc",
  surfaceDim: "#060910",
  surfaceBright: "#1e293b",
  surfaceContainerLowest: "#060910",
  surfaceContainerLow: "#0f172a",
  surfaceContainer: "#111827",
  surfaceContainerHigh: "#1e293b",
  surfaceContainerHighest: "#334155",
  outline: "#475569",
  outlineVariant: "#1e293b",
  inverseSurface: "#f8fafc",
  inverseOnSurface: "#0f172a",
  inversePrimary: "#2563eb",
  scrim: "#000000",
  shadow: "rgba(0, 0, 0, 0.5)",
};

export const md3LightColors: MD3ColorSystem = {
  primary: "#2563eb",
  onPrimary: "#ffffff",
  primaryContainer: "rgba(37, 99, 235, 0.12)",
  onPrimaryContainer: "#1e3a8a",
  secondary: "#7c3aed",
  onSecondary: "#ffffff",
  secondaryContainer: "rgba(124, 58, 237, 0.12)",
  onSecondaryContainer: "#4c1d95",
  tertiary: "#059669",
  onTertiary: "#ffffff",
  tertiaryContainer: "rgba(5, 150, 105, 0.12)",
  onTertiaryContainer: "#064e3b",
  surface: "#f8fafc",
  onSurface: "#0f172a",
  surfaceDim: "#f1f5f9",
  surfaceBright: "#ffffff",
  surfaceContainerLowest: "#ffffff",
  surfaceContainerLow: "#f8fafc",
  surfaceContainer: "#ffffff",
  surfaceContainerHigh: "#f1f5f9",
  surfaceContainerHighest: "#e2e8f0",
  outline: "#94a3b8",
  outlineVariant: "#e2e8f0",
  inverseSurface: "#0f172a",
  inverseOnSurface: "#f8fafc",
  inversePrimary: "#60a5fa",
  scrim: "#000000",
  shadow: "rgba(15, 23, 42, 0.12)",
};

export const md3Elevation = {
  level0: "none",
  level1: "0 1px 3px 0 rgba(0, 0, 0, 0.25), 0 1px 2px -1px rgba(0, 0, 0, 0.2)",
  level2: "0 4px 6px -1px rgba(0, 0, 0, 0.3), 0 2px 4px -2px rgba(0, 0, 0, 0.2)",
  level3: "0 10px 15px -3px rgba(0, 0, 0, 0.35), 0 4px 6px -4px rgba(0, 0, 0, 0.2)",
  level4: "0 20px 25px -5px rgba(0, 0, 0, 0.4), 0 8px 10px -6px rgba(0, 0, 0, 0.2)",
  level5: "0 25px 50px -12px rgba(0, 0, 0, 0.5)",
};

export const md3Shapes = {
  xs: "4px",
  sm: "8px",
  md: "12px",
  lg: "16px",
  xl: "28px",
  full: "9999px",
};
