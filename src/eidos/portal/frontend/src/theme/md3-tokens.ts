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
  primary: "#7cb3ff",
  onPrimary: "#0d1117",
  primaryContainer: "rgba(109, 166, 255, 0.15)",
  onPrimaryContainer: "#c8dfff",
  secondary: "#b19bfa",
  onSecondary: "#120d26",
  secondaryContainer: "rgba(155, 130, 248, 0.16)",
  onSecondaryContainer: "#e3d9ff",
  tertiary: "#4ecba0",
  onTertiary: "#062118",
  tertiaryContainer: "rgba(78, 203, 160, 0.15)",
  onTertiaryContainer: "#baf0da",
  surface: "#1c2026",
  onSurface: "#e6edf3",
  surfaceDim: "#161a20",
  surfaceBright: "#323946",
  surfaceContainerLowest: "#161a20",
  surfaceContainerLow: "#1f242b",
  surfaceContainer: "#22272f",
  surfaceContainerHigh: "#282e38",
  surfaceContainerHighest: "#323946",
  outline: "#7c8594",
  outlineVariant: "#323946",
  inverseSurface: "#fafafa",
  inverseOnSurface: "#1f2328",
  inversePrimary: "#1d5fd1",
  scrim: "#000000",
  shadow: "rgba(0, 0, 0, 0.45)",
};

export const md3LightColors: MD3ColorSystem = {
  primary: "#1d5fd1",
  onPrimary: "#ffffff",
  primaryContainer: "rgba(29, 95, 209, 0.11)",
  onPrimaryContainer: "#153e8a",
  secondary: "#6d4aff",
  onSecondary: "#ffffff",
  secondaryContainer: "rgba(109, 74, 255, 0.11)",
  onSecondaryContainer: "#3e259e",
  tertiary: "#157f59",
  onTertiary: "#ffffff",
  tertiaryContainer: "rgba(21, 127, 89, 0.11)",
  onTertiaryContainer: "#0b4a33",
  surface: "#fafafa",
  onSurface: "#1f2328",
  surfaceDim: "#f0f2f5",
  surfaceBright: "#ffffff",
  surfaceContainerLowest: "#ffffff",
  surfaceContainerLow: "#fafafa",
  surfaceContainer: "#ffffff",
  surfaceContainerHigh: "#f0f2f5",
  surfaceContainerHighest: "#e4e7ec",
  outline: "#8b949e",
  outlineVariant: "#e1e4e8",
  inverseSurface: "#1c2026",
  inverseOnSurface: "#e6edf3",
  inversePrimary: "#7cb3ff",
  scrim: "#000000",
  shadow: "rgba(31, 35, 40, 0.10)",
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
