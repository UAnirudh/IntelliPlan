import { useState } from "react";
import { ActivityIndicator, Alert, KeyboardAvoidingView, Platform, Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { useRouter } from "expo-router";
import { login } from "../lib/api";

export default function LoginScreen() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (!email || !password) return;
    setBusy(true);
    try {
      await login(email.trim().toLowerCase(), password);
      router.replace("/(tabs)/today");
    } catch (e: any) {
      Alert.alert("Sign in failed", e?.message || "Check your email and password.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={styles.wrap}>
      <View style={styles.card}>
        <Text style={styles.title}>IntelliPlan</Text>
        <Text style={styles.subtitle}>Sign in to sync your day.</Text>
        <TextInput
          style={styles.input}
          placeholder="Email"
          autoCapitalize="none"
          keyboardType="email-address"
          value={email}
          onChangeText={setEmail}
        />
        <TextInput
          style={styles.input}
          placeholder="Password"
          secureTextEntry
          value={password}
          onChangeText={setPassword}
        />
        <Pressable onPress={submit} disabled={busy} style={[styles.btn, busy && styles.btnBusy]}>
          {busy ? <ActivityIndicator color="#fff" /> : <Text style={styles.btnText}>Sign in</Text>}
        </Pressable>
      </View>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  wrap: { flex: 1, justifyContent: "center", padding: 24, backgroundColor: "#f5f4f1" },
  card: { backgroundColor: "#fff", borderRadius: 18, padding: 24, gap: 12, shadowColor: "#000", shadowOpacity: 0.06, shadowRadius: 20, elevation: 3 },
  title: { fontSize: 26, fontWeight: "700", color: "#1a1a1a" },
  subtitle: { color: "#666", marginBottom: 8 },
  input: { borderWidth: 1, borderColor: "#e5e3df", borderRadius: 10, padding: 12, fontSize: 16, color: "#1a1a1a" },
  btn: { backgroundColor: "#1a1a1a", padding: 14, borderRadius: 999, alignItems: "center", marginTop: 8 },
  btnBusy: { opacity: 0.7 },
  btnText: { color: "#fff", fontWeight: "600", fontSize: 16 },
});
