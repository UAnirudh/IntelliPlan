import React, { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import { Modal, Pressable, View } from "react-native";
import { useTheme } from "../theme/ThemeProvider";
import { radius, space } from "../theme/tokens";
import { T } from "./ui";

/**
 * Ask the student something, on every platform.
 *
 * React Native's own `Alert.alert()` is a no-op on react-native-web — the
 * implementation is literally an empty method — so every confirmation
 * built on it (sign out, give up on a session, delete a chat) silently did
 * nothing in the browser build. Rather than keeping two code paths and
 * letting the web one rot, this renders one themed sheet everywhere: it
 * looks like the rest of the app, it can be driven in a test, and there is
 * only one implementation to be correct.
 */

export type ConfirmAction = {
  label: string;
  /** Red, and never the default. */
  destructive?: boolean;
  /** Dismisses without running anything; there is at most one. */
  cancel?: boolean;
};

type Request = {
  title: string;
  message?: string;
  actions: ConfirmAction[];
};

type Ctx = {
  /** Resolves with the index of the action chosen, or -1 if dismissed. */
  ask: (req: Request) => Promise<number>;
};

const ConfirmCtx = createContext<Ctx>({ ask: async () => -1 });

export function ConfirmProvider({ children }: { children: React.ReactNode }) {
  const { colors } = useTheme();
  const [request, setRequest] = useState<Request | null>(null);
  const resolver = useRef<((i: number) => void) | null>(null);

  const ask = useCallback((req: Request) => {
    return new Promise<number>((resolve) => {
      // A second question arriving while one is open would strand the first
      // promise forever, and an un-awaited caller is a hang with no error.
      if (resolver.current) resolver.current(-1);
      resolver.current = resolve;
      setRequest(req);
    });
  }, []);

  const settle = useCallback((index: number) => {
    const done = resolver.current;
    resolver.current = null;
    setRequest(null);
    done?.(index);
  }, []);

  const value = useMemo<Ctx>(() => ({ ask }), [ask]);

  return (
    <ConfirmCtx.Provider value={value}>
      {children}
      <Modal
        visible={!!request}
        transparent
        animationType="fade"
        onRequestClose={() => settle(-1)}
      >
        <Pressable
          onPress={() => settle(-1)}
          accessibilityRole="button"
          accessibilityLabel="Dismiss"
          style={{
            flex: 1,
            backgroundColor: colors.scrim,
            alignItems: "center",
            justifyContent: "center",
            padding: space.xl,
          }}
        >
          {/* Swallows the tap so pressing inside the card does not count as
              tapping the backdrop behind it. */}
          <Pressable
            onPress={() => {}}
            style={{
              width: "100%",
              maxWidth: 380,
              backgroundColor: colors.bgCard,
              borderRadius: radius.xl,
              borderWidth: 1,
              borderColor: colors.border,
              padding: space.xl,
              gap: space.md,
            }}
          >
            <T variant="md" weight="700">
              {request?.title}
            </T>
            {request?.message ? (
              <T variant="sm" tone="secondary">
                {request.message}
              </T>
            ) : null}

            <View style={{ gap: space.sm, marginTop: space.sm }}>
              {(request?.actions || []).map((a, i) => {
                // Only the first non-cancel action is filled. Two solid
                // primaries side by side give the eye nothing to land on,
                // and every call site would otherwise have to remember to
                // say so — this makes the common shape right by default.
                const isLead =
                  !a.cancel &&
                  !a.destructive &&
                  (request?.actions || []).findIndex((x) => !x.cancel && !x.destructive) === i;
                const fg = a.destructive
                  ? colors.dangerText
                  : a.cancel
                    ? colors.textSecondary
                    : isLead
                      ? colors.onAccent
                      : colors.textPrimary;
                const bg = a.destructive
                  ? colors.dangerSoft
                  : a.cancel
                    ? "transparent"
                    : isLead
                      ? colors.accent
                      : colors.bgSecondary;
                return (
                  <Pressable
                    key={`${a.label}-${i}`}
                    onPress={() => settle(i)}
                    accessibilityRole="button"
                    style={({ pressed }) => ({
                      backgroundColor: bg,
                      borderRadius: radius.pill,
                      borderWidth: a.cancel ? 0 : 1,
                      borderColor: a.destructive
                        ? colors.danger
                        : isLead
                          ? colors.accent
                          : colors.borderStrong,
                      paddingVertical: 13,
                      alignItems: "center",
                      opacity: pressed ? 0.8 : 1,
                    })}
                  >
                    <T variant="base" weight="600" style={{ color: fg }}>
                      {a.label}
                    </T>
                  </Pressable>
                );
              })}
            </View>
          </Pressable>
        </Pressable>
      </Modal>
    </ConfirmCtx.Provider>
  );
}

export function useConfirm() {
  return useContext(ConfirmCtx).ask;
}
