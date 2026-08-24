import React from "react";
import { ScrollView, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import Constants from "expo-constants";
import { useTheme } from "../theme/ThemeProvider";
import { radius, space } from "../theme/tokens";
import { Button, Card, Label, Screen, T } from "./ui";

/**
 * What the student sees when the app throws.
 *
 * expo-router renders this in place of a segment whose render threw. Without
 * it, a release build shows a blank white screen with no way forward — the
 * red box with the stack trace is a development-only courtesy, and the
 * store build has nothing in its place.
 *
 * Retry first, because most of what can throw here is a render against a
 * payload shaped differently than expected, and a re-render after a
 * refetch usually clears it.
 */
export function AppErrorBoundary({ error, retry }: { error: Error; retry: () => Promise<void> }) {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const [busy, setBusy] = React.useState(false);

  return (
    <Screen>
      <ScrollView
        contentContainerStyle={{
          flexGrow: 1,
          justifyContent: "center",
          padding: space.xl,
          paddingTop: insets.top + space.xl,
          paddingBottom: insets.bottom + space.xl,
          gap: space.lg,
        }}
      >
        <View style={{ alignItems: "center", gap: space.md }}>
          <View
            style={{
              width: 60,
              height: 60,
              borderRadius: radius.pill,
              backgroundColor: colors.warnSoft,
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <Ionicons name="warning-outline" size={27} color={colors.warnText} />
          </View>
          <T variant="lg" weight="700" style={{ textAlign: "center" }}>
            That screen hit a snag
          </T>
          <T tone="secondary" style={{ textAlign: "center", maxWidth: 320 }}>
            Nothing you've done is lost — your work lives on the server, not in
            this screen.
          </T>
        </View>

        <Button
          title="Try again"
          icon="refresh"
          busy={busy}
          onPress={async () => {
            setBusy(true);
            try {
              await retry();
            } finally {
              setBusy(false);
            }
          }}
        />

        {/* The message, not the stack: a student cannot act on a stack trace,
            but they can read it out to us, and it is the one thing that makes
            a bug report reproducible. */}
        <Card style={{ gap: 6 }}>
          <Label>If it keeps happening, send us this</Label>
          <T variant="xs" tone="muted" selectable>
            {String(error?.message || error || "Unknown error")}
          </T>
          <T variant="xs" tone="muted">
            IntelliPlan {Constants.expoConfig?.version ?? "—"}
          </T>
        </Card>
      </ScrollView>
    </Screen>
  );
}
