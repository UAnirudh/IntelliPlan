import React, { useCallback, useMemo, useState } from "react";
import { Pressable, RefreshControl, ScrollView, View } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import * as Haptics from "expo-haptics";
import {
  buyShopItem,
  getStreak,
  repairStreak,
  Shop,
  ShopItem,
  Streak,
} from "../lib/api";
import { useQuery } from "../lib/useQuery";
import { useConfirm } from "../components/Confirm";
import { useTheme } from "../theme/ThemeProvider";
import { radius, space } from "../theme/tokens";
import {
  Button,
  Card,
  Chip,
  ErrorState,
  Label,
  Loading,
  Notice,
  Screen,
  SegmentedRow,
  T,
} from "../components/ui";

/**
 * The streak, and what Sparks are for.
 *
 * Everything on this screen comes from one `/study/points` read — balance,
 * streak state, whether a repair window is open, and the whole shop
 * catalogue including the week's discounted item. Nothing here keeps its
 * own copy of a price: a shop hard-coded in the app is a shop that lies
 * the first time the server changes one.
 */

/** The catalogue is a flat dict; these are the groups worth showing it in. */
const GROUPS: { key: string; label: string; blurb: string }[] = [
  { key: "protection", label: "Protection", blurb: "Cover a day you can't study." },
  { key: "booster", label: "Boosters", blurb: "Earn Sparks faster for a while." },
  { key: "inventory", label: "Helpers", blurb: "Skips, hints, and a cheaper repair." },
  { key: "cosmetic", label: "Looks", blurb: "Colours, frames and titles." },
];

export default function StreakScreen() {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const confirm = useConfirm();

  const q = useQuery<Streak>("streak", getStreak);
  const [group, setGroup] = useState("protection");
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const shop = (q.data?.shop as Shop | undefined) || {};
  const balance = Number(q.data?.spark_balance || 0);
  const dealId = shop.weekly_deal?.item_id;

  /** What each item actually costs this week, deal included. */
  const priceOf = useCallback(
    (id: string, item: ShopItem) =>
      id === dealId && shop.weekly_deal?.price != null
        ? Number(shop.weekly_deal.price)
        : Number(item.price),
    [dealId, shop.weekly_deal],
  );

  const items = useMemo(() => {
    const all = Object.entries(shop.items || {});
    return all
      .filter(([, it]) => (it.kind || "cosmetic") === group)
      .sort((a, b) => priceOf(a[0], a[1]) - priceOf(b[0], b[1]));
  }, [shop.items, group, priceOf]);

  const buy = useCallback(
    async (id: string, item: ShopItem) => {
      const price = priceOf(id, item);
      const choice = await confirm({
        title: `Buy ${item.name}?`,
        message: `${price} Sparks. You'll have ${Math.max(0, balance - price)} left.`,
        actions: [{ label: "Buy" }, { label: "Cancel", cancel: true }],
      });
      if (choice !== 0) return;

      setBusy(id);
      setNote(null);
      try {
        const r = await buyShopItem(id);
        // Refetch rather than patching the balance locally: a purchase can
        // change freezes, boosters, cosmetics and badges at once, and
        // guessing at all of that here is how the two drift apart.
        await q.reload();
        setNote(
          r.badges_unlocked?.length
            ? `${item.name} bought — and you unlocked ${r.badges_unlocked.join(", ")}.`
            : `${item.name} bought.`,
        );
        Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {});
      } catch (e: any) {
        setNote(e?.message || "Couldn't buy that.");
        Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error).catch(() => {});
      } finally {
        setBusy(null);
      }
    },
    [balance, confirm, priceOf, q],
  );

  const repair = useCallback(async () => {
    const cost = Number(q.data?.repair_cost || 0);
    const choice = await confirm({
      title: "Repair your streak?",
      message: `${cost} Sparks brings your ${q.data?.longest_streak || 0}-day streak back. Repair can only be used once every 30 days.`,
      actions: [{ label: "Repair" }, { label: "Cancel", cancel: true }],
    });
    if (choice !== 0) return;

    setBusy("repair");
    setNote(null);
    try {
      const r = await repairStreak();
      await q.reload();
      setNote(
        r.used_repair_credit
          ? `Streak repaired for ${r.repair_cost} Sparks — half price, using a Repair Token.`
          : `Streak repaired. You're back to ${r.streak_count} days.`,
      );
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {});
    } catch (e: any) {
      setNote(e?.message || "Couldn't repair that streak.");
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error).catch(() => {});
    } finally {
      setBusy(null);
    }
  }, [confirm, q]);

  const header = (
    <View
      style={{
        flexDirection: "row",
        alignItems: "center",
        paddingTop: insets.top + space.sm,
        paddingHorizontal: space.lg,
        paddingBottom: space.md,
        borderBottomWidth: 1,
        borderBottomColor: colors.border,
      }}
    >
      <View style={{ flex: 1 }}>
        <T variant="lg" weight="700">Streak &amp; shop</T>
        <T variant="sm" tone="muted">{balance.toLocaleString()} Sparks</T>
      </View>
      <Pressable onPress={() => router.back()} hitSlop={10} accessibilityRole="button" accessibilityLabel="Close">
        <Ionicons name="close" size={26} color={colors.textSecondary} />
      </Pressable>
    </View>
  );

  if (q.loading) {
    return (
      <Screen>
        {header}
        <Loading label="Loading your streak…" />
      </Screen>
    );
  }

  if (q.error && !q.data) {
    return (
      <Screen>
        {header}
        <ErrorState message={q.error} onRetry={q.reload} />
      </Screen>
    );
  }

  const streakCount = Number(q.data?.streak_count || 0);
  const freezes = Number(q.data?.streak_freeze_count || 0);
  const capacity = Number(q.data?.freeze_capacity || 0);

  return (
    <Screen>
      {header}
      <ScrollView
        contentContainerStyle={{ padding: space.lg, gap: space.md, paddingBottom: insets.bottom + 60 }}
        refreshControl={
          <RefreshControl refreshing={q.refreshing} onRefresh={q.refresh} tintColor={colors.accent} />
        }
      >
        {q.stale ? (
          <Notice text="Offline — showing the last figures that loaded." icon="cloud-offline-outline" />
        ) : null}
        {note ? <Notice tone="accent" text={note} icon="information-circle-outline" /> : null}

        <Card style={{ gap: space.md }}>
          <View style={{ flexDirection: "row", alignItems: "center", gap: space.md }}>
            <View
              style={{
                width: 56,
                height: 56,
                borderRadius: radius.md,
                backgroundColor: colors.accentSoft,
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <Ionicons name="flame" size={28} color={colors.accent} />
            </View>
            <View style={{ flex: 1 }}>
              <T variant="xl" weight="700">
                {streakCount} day{streakCount === 1 ? "" : "s"}
              </T>
              <T variant="sm" tone="muted">
                {q.data?.level_title ? `${q.data.level_title} · ` : ""}
                Longest {q.data?.longest_streak || streakCount}
              </T>
            </View>
          </View>

          <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 6 }}>
            <Chip label={`${balance.toLocaleString()} Sparks`} icon="sparkles-outline" />
            {capacity ? (
              <Chip label={`${freezes}/${capacity} freezes`} icon="snow-outline" />
            ) : null}
            {q.data?.total_sessions ? (
              <Chip label={`${q.data.total_sessions} sessions`} icon="timer-outline" />
            ) : null}
          </View>

          {/* Shown before the day is lost, not after. A warning that arrives
              with the repair offer is not a warning. */}
          {q.data?.at_risk ? (
            <Notice
              icon="alert-circle-outline"
              text="Your streak is still open today. One session keeps it alive."
            />
          ) : null}
        </Card>

        {q.data?.repair_available ? (
          <Card style={{ gap: space.md, borderColor: colors.borderAccent, backgroundColor: colors.accentSoft }}>
            <View style={{ gap: 4 }}>
              <Label>Streak repair</Label>
              <T variant="sm" tone="secondary">
                Your {q.data.longest_streak || 0}-day streak broke, and the window to buy it
                back is still open.
              </T>
            </View>
            <Button
              title={`Repair for ${q.data.repair_cost ?? 0} Sparks`}
              icon="bandage-outline"
              busy={busy === "repair"}
              disabled={balance < Number(q.data.repair_cost || 0)}
              onPress={repair}
            />
            {balance < Number(q.data.repair_cost || 0) ? (
              <T variant="xs" tone="muted" style={{ textAlign: "center" }}>
                You need {Number(q.data.repair_cost || 0) - balance} more Sparks.
              </T>
            ) : null}
          </Card>
        ) : null}

        {Object.keys(shop.items || {}).length ? (
          <>
            <SegmentedRow
              options={GROUPS.map((g) => ({ label: g.label, value: g.key }))}
              value={group}
              onChange={setGroup}
            />
            <T variant="sm" tone="muted">
              {GROUPS.find((g) => g.key === group)?.blurb}
            </T>

            {items.map(([id, item]) => {
              const price = priceOf(id, item);
              const onDeal = id === dealId;
              const affordable = balance >= price;
              return (
                <Card key={id} style={{ gap: space.sm }}>
                  <View style={{ flexDirection: "row", alignItems: "flex-start", gap: space.sm }}>
                    <View style={{ flex: 1, gap: 4 }}>
                      <T variant="base" weight="700">{item.name}</T>
                      {item.description ? (
                        <T variant="sm" tone="secondary">{item.description}</T>
                      ) : null}
                    </View>
                    <View style={{ alignItems: "flex-end", gap: 4 }}>
                      <T variant="base" weight="700">{price}</T>
                      {onDeal ? (
                        <T
                          variant="xs"
                          tone="muted"
                          style={{ textDecorationLine: "line-through" }}
                        >
                          {item.price}
                        </T>
                      ) : null}
                    </View>
                  </View>
                  {onDeal ? (
                    <Chip
                      label={`${shop.weekly_deal?.discount_percent ?? 30}% off this week`}
                      icon="pricetag-outline"
                      fg={colors.ok}
                      bg={colors.okSoft}
                    />
                  ) : null}
                  <Button
                    title={affordable ? "Buy" : `Need ${price - balance} more Sparks`}
                    kind="secondary"
                    busy={busy === id}
                    disabled={!affordable}
                    onPress={() => buy(id, item)}
                  />
                </Card>
              );
            })}
          </>
        ) : (
          <Notice
            text="The shop isn't available on this account yet."
            icon="information-circle-outline"
          />
        )}
      </ScrollView>
    </Screen>
  );
}
