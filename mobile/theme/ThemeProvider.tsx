import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { useColorScheme } from "react-native";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { dark, light, Palette } from "./tokens";

export type ThemeMode = "system" | "light" | "dark";

type Ctx = {
  colors: Palette;
  scheme: "light" | "dark";
  mode: ThemeMode;
  setMode: (m: ThemeMode) => void;
};

const PREF_KEY = "ip_theme_mode";

const ThemeCtx = createContext<Ctx>({
  colors: light,
  scheme: "light",
  mode: "system",
  setMode: () => {},
});

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const system = useColorScheme();
  const [mode, setModeState] = useState<ThemeMode>("system");

  // Read the stored preference once. Until it lands the app renders in the
  // system scheme, which is the same thing "system" resolves to — so the
  // common case shows no flash, and only an explicit override repaints.
  useEffect(() => {
    AsyncStorage.getItem(PREF_KEY)
      .then((v) => {
        if (v === "light" || v === "dark" || v === "system") setModeState(v);
      })
      .catch(() => {});
  }, []);

  const setMode = useCallback((m: ThemeMode) => {
    setModeState(m);
    AsyncStorage.setItem(PREF_KEY, m).catch(() => {});
  }, []);

  const scheme: "light" | "dark" =
    mode === "system" ? (system === "dark" ? "dark" : "light") : mode;

  const value = useMemo<Ctx>(
    () => ({ colors: scheme === "dark" ? dark : light, scheme, mode, setMode }),
    [scheme, mode, setMode],
  );

  return <ThemeCtx.Provider value={value}>{children}</ThemeCtx.Provider>;
}

export function useTheme() {
  return useContext(ThemeCtx);
}
