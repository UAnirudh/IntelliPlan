import React, { useCallback, useState } from "react";
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, View } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import * as Haptics from "expo-haptics";
import * as WebBrowser from "expo-web-browser";
import { startLinkSession } from "../../lib/api";
import { NavItem, SIDEBAR, SIDEBAR_FOOT } from "../../lib/nav";
import { useAuth } from "../../lib/auth";
import { useTheme } from "../../theme/ThemeProvider";
import { radius, space } from "../../theme/tokens";
import { Card, Label, Notice, Screen, T } from "../../components/ui";
import { GlassSurface } from "../../components/glass";
import { Header } from "../../components/Header";
import { useConfirm } from "../../components/Confirm";

/**
 * Everything in the website sidebar, on the phone.
 *
 * A tab bar cannot hold thirteen destinations, so it keeps the daily five
 * and this screen carries the whole sidebar in the website's own order.
 * Pages the app has a native screen for open natively; the rest open the
 * real web page in an in-app browser that is already signed in, so nothing
 * on the website is out of reach from the phone.
 */
export default function MoreScreen() {
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { signOut } = useAuth();
  const confirm = useConfirm();
  const [opening, setOpening] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const open = useCallback(
    async (item: NavItem) => {
      Haptics.selectionAsync().catch(() => {});
      setError(null);
      if (item.route) {
        router.push(item.route as never);
        return;
      }
      if (!item.page || opening) return;
      setOpening(item.key);
      try {
        const session = await startLinkSession(item.page);
        await WebBrowser.openBrowserAsync(session.url, {
          presentationStyle: WebBrowser.WebBrowserPresentationStyle.PAGE_SHEET,
        });
      } catch (e: unknown) {
        setError(
          e instanceof Error && e.message
            ? `Couldn't open ${item.label}: ${e.message}`
            : `Couldn't open ${item.label}. Check your connection and try again.`,
        );
      } finally {
        setOpening(null);
      }
    },
    [router, opening],
  );

  const confirmSignOut = useCallback(async () => {
    const choice = await confirm({
      title: "Sign out?",
      message: "You'll need your email and password to get back in.",
      actions: [
        { label: "Sign out", destructive: true },
        { label: "Cancel", cancel: true },
      ],
    });
    if (choice !== 0) return;
    await signOut();
    router.replace("/login");
  }, [confirm, signOut, router]);

  return (
    <Screen>
      <Header title="Menu" subtitle="Everything on the website" showSettings={false} />
      <ScrollView
        contentContainerStyle={{
          padding: space.lg,
          paddingBottom: insets.bottom + 100,
          gap: space.md,
        }}
      >
        {error ? <Notice text={error} icon="alert-circle-outline" /> : null}

        <Group items={SIDEBAR} opening={opening} onOpen={open} />

        <Label style={{ marginTop: space.sm, marginLeft: space.xs }}>Account</Label>
        <Group items={SIDEBAR_FOOT} opening={opening} onOpen={open}>
          <Row
            icon="log-out-outline"
            label="Logout"
            danger
            onPress={confirmSignOut}
          />
        </Group>

        <T variant="xs" tone="muted" style={{ textAlign: "center", marginTop: space.sm }}>
          Pages marked with the open icon load the website, already signed in.
        </T>
      </ScrollView>
    </Screen>
  );
}

/** One glass panel of rows, the iOS inset-grouped list shape. */
function Group({
  items,
  opening,
  onOpen,
  children,
}: {
  items: NavItem[];
  opening: string | null;
  onOpen: (item: NavItem) => void;
  children?: React.ReactNode;
}) {
  return (
    <Card style={{ padding: 0, overflow: "hidden" }}>
      {items.map((item, i) => (
        <Row
          key={item.key}
          icon={item.icon}
          label={item.label}
          blurb={item.blurb}
          external={!item.route}
          busy={opening === item.key}
          first={i === 0}
          onPress={() => onOpen(item)}
        />
      ))}
      {children}
    </Card>
  );
}

function Row({
  icon,
  label,
  blurb,
  external,
  busy,
  danger,
  first,
  onPress,
}: {
  icon: NavItem["icon"];
  label: string;
  blurb?: string;
  external?: boolean;
  busy?: boolean;
  danger?: boolean;
  first?: boolean;
  onPress: () => void;
}) {
  const { colors } = useTheme();
  const fg = danger ? colors.dangerText : colors.textPrimary;

  return (
    <Pressable
      onPress={onPress}
      disabled={busy}
      accessibilityRole={external ? "link" : "button"}
      accessibilityLabel={external ? `${label}, opens the website` : label}
      accessibilityHint={blurb}
      style={({ pressed }) => ({
        flexDirection: "row",
        alignItems: "center",
        gap: space.md,
        paddingHorizontal: space.lg,
        paddingVertical: space.md,
        borderTopWidth: first ? 0 : StyleSheet.hairlineWidth,
        borderTopColor: colors.border,
        backgroundColor: pressed ? colors.accentSoft : "transparent",
      })}
    >
      <GlassSurface
        variant="clear"
        tint={danger ? colors.dangerSoft : colors.accentSoft}
        style={{
          width: 34,
          height: 34,
          borderRadius: radius.md - 2,
          backgroundColor: danger ? colors.dangerSoft : colors.accentSoft,
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <Ionicons name={icon} size={18} color={danger ? colors.dangerText : colors.accent} />
      </GlassSurface>

      <View style={{ flex: 1 }}>
        <T variant="base" weight="600" style={{ color: fg }}>
          {label}
        </T>
        {blurb ? (
          <T variant="xs" tone="muted" numberOfLines={1}>
            {blurb}
          </T>
        ) : null}
      </View>

      {busy ? (
        <ActivityIndicator size="small" color={colors.accent} />
      ) : danger ? null : (
        <Ionicons
          name={external ? "open-outline" : "chevron-forward"}
          size={17}
          color={colors.textMuted}
        />
      )}
    </Pressable>
  );
}
