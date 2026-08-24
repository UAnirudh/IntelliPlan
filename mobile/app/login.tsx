import { useState } from "react";
import {
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  View,
} from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { useAuth } from "../lib/auth";
import { markOnboarded } from "../lib/onboarding";
import { useTheme } from "../theme/ThemeProvider";
import { radius, space } from "../theme/tokens";
import { Button, Field, Notice, Screen, T } from "../components/ui";

type Mode = "signin" | "signup";

export default function LoginScreen() {
  const router = useRouter();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const { signIn, signUp } = useAuth();

  const [mode, setMode] = useState<Mode>("signin");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const signup = mode === "signup";

  async function submit() {
    setError(null);
    if (!email.trim() || !password) {
      setError("Email and password are both required.");
      return;
    }
    // The server enforces this too, but a round trip to be told the
    // password is short is a slow way to learn it.
    if (signup && password.length < 6) {
      setError("Pick a password of at least 6 characters.");
      return;
    }
    setBusy(true);
    try {
      if (signup) {
        // A brand-new account has nothing connected and nothing due, so
        // it goes straight to setup rather than to five empty tabs. The
        // onboarded flag stays unset until they finish or skip, so
        // force-quitting mid-setup resumes there rather than dropping
        // them into an app they have not configured.
        await signUp(email, password, name);
        router.replace("/onboarding");
      } else {
        // Signing in means the account already exists, so the tour has
        // nothing to tell them — they are only here on this device for
        // the first time. Marking it now stops the per-device flag from
        // re-running setup for someone who has been using IntelliPlan
        // for months.
        const me = await signIn(email, password);
        if (me?.id !== undefined) await markOnboarded(me.id);
        router.replace("/(tabs)/today");
      }
    } catch (e: any) {
      setError(e?.message || "That didn't work. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Screen>
      <KeyboardAvoidingView
        behavior={Platform.OS === "ios" ? "padding" : undefined}
        style={{ flex: 1 }}
      >
        <ScrollView
          contentContainerStyle={{
            flexGrow: 1,
            justifyContent: "center",
            padding: space.xl,
            paddingTop: insets.top + space.xl,
            paddingBottom: insets.bottom + space.xl,
            gap: space.xl,
          }}
          keyboardShouldPersistTaps="handled"
        >
          <View style={{ gap: space.sm }}>
            <View
              style={{
                width: 52,
                height: 52,
                borderRadius: radius.lg,
                backgroundColor: colors.accent,
                alignItems: "center",
                justifyContent: "center",
                marginBottom: space.sm,
              }}
            >
              <Ionicons name="school" size={27} color={colors.onAccent} />
            </View>
            <T variant="xxl" weight="700">
              {signup ? "Start planning" : "Welcome back"}
            </T>
            <T tone="secondary">
              {signup
                ? "One account works here, on the website, and in the browser extension."
                : "Sign in to sync your day across every device."}
            </T>
          </View>

          <View style={{ gap: space.md }}>
            {signup ? (
              <Field
                label="Name"
                placeholder="Your name"
                autoCapitalize="words"
                autoComplete="name"
                value={name}
                onChangeText={setName}
                returnKeyType="next"
              />
            ) : null}

            <Field
              label="Email"
              placeholder="you@school.edu"
              autoCapitalize="none"
              autoCorrect={false}
              keyboardType="email-address"
              autoComplete="email"
              textContentType="emailAddress"
              value={email}
              onChangeText={setEmail}
              returnKeyType="next"
            />

            <View>
              <Field
                label="Password"
                placeholder={signup ? "At least 6 characters" : "Your password"}
                secureTextEntry={!show}
                autoCapitalize="none"
                autoComplete={signup ? "new-password" : "current-password"}
                textContentType={signup ? "newPassword" : "password"}
                value={password}
                onChangeText={setPassword}
                returnKeyType="go"
                onSubmitEditing={submit}
              />
              <Pressable
                onPress={() => setShow((s) => !s)}
                hitSlop={10}
                accessibilityRole="button"
                accessibilityLabel={show ? "Hide password" : "Show password"}
                style={{ position: "absolute", right: 12, bottom: 12 }}
              >
                <Ionicons
                  name={show ? "eye-off-outline" : "eye-outline"}
                  size={20}
                  color={colors.textMuted}
                />
              </Pressable>
            </View>

            {error ? <Notice text={error} tone="warn" icon="alert-circle-outline" /> : null}

            <Button
              title={signup ? "Create account" : "Sign in"}
              busy={busy}
              onPress={submit}
              style={{ marginTop: space.xs }}
            />

            <Pressable
              onPress={() => {
                setMode(signup ? "signin" : "signup");
                setError(null);
              }}
              hitSlop={8}
              accessibilityRole="button"
              style={{ alignSelf: "center", padding: space.sm }}
            >
              <T variant="sm" tone="accent" weight="600">
                {signup ? "I already have an account" : "New here? Create an account"}
              </T>
            </Pressable>
          </View>

          <T variant="xs" tone="muted" style={{ textAlign: "center" }}>
            Signed in with Google on the website? Set a password from Settings
            there first — this app signs in with email and password.
          </T>
        </ScrollView>
      </KeyboardAvoidingView>
    </Screen>
  );
}
