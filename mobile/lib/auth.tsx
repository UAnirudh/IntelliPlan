import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import AsyncStorage from "@react-native-async-storage/async-storage";
import {
  ApiError,
  getMe,
  getToken,
  login as apiLogin,
  logout as apiLogout,
  Me,
  onUnauthorized,
  register as apiRegister,
} from "./api";
import { clearQueryCache } from "./useQuery";
import { disablePush } from "./push";

type AuthState = {
  user: Me | null;
  /** Still deciding whether there is a session — render nothing until false. */
  booting: boolean;
  signIn: (email: string, password: string) => Promise<Me>;
  signUp: (email: string, password: string, name?: string) => Promise<Me>;
  signOut: () => Promise<void>;
};

/** Last profile the server confirmed, so an offline launch still knows
 *  whose day it is showing. */
const USER_KEY = "ip_last_user";

async function cacheUser(u: Me | null) {
  try {
    if (u) await AsyncStorage.setItem(USER_KEY, JSON.stringify(u));
    else await AsyncStorage.removeItem(USER_KEY);
  } catch {
    /* a profile we cannot cache is not worth failing sign-in over */
  }
}

async function cachedUser(): Promise<Me | null> {
  try {
    const raw = await AsyncStorage.getItem(USER_KEY);
    return raw ? (JSON.parse(raw) as Me) : null;
  } catch {
    return null;
  }
}

const Ctx = createContext<AuthState>({
  user: null,
  booting: true,
  signIn: async () => ({ id: -1, email: "" }),
  signUp: async () => ({ id: -1, email: "" }),
  signOut: async () => {},
});

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<Me | null>(null);
  const [booting, setBooting] = useState(true);

  const signOut = useCallback(async () => {
    // Tell the server to stop pushing to this device *before* dropping the
    // token — the unregister call needs it. On a shared phone, skipping
    // this would keep sending one student's deadlines to the next one.
    await disablePush().catch(() => {});
    await apiLogout();
    await cacheUser(null);
    await clearQueryCache().catch(() => {});
    setUser(null);
  }, []);

  useEffect(() => {
    (async () => {
      const token = await getToken();
      if (!token) {
        setBooting(false);
        return;
      }
      try {
        // A stored token is only worth trusting if the server still
        // accepts it: it is signed and long-lived, so it survives a
        // password change and a deleted account unless we ask.
        const me = await getMe();
        setUser(me);
        cacheUser(me);
      } catch (e) {
        const err = e as ApiError;
        if (err?.isAuth) {
          // The server actively rejected the credential. Drop it.
          await apiLogout();
          setUser(null);
        } else {
          // Anything else — no signal, a timeout, a 502 during a deploy —
          // says nothing about whether the token is still good. Signing
          // out here would mean a student who opened the app on the bus
          // has to find their password to get back in, and their cached
          // day is right there. Keep the session and let the screens fall
          // back to cache; a request that really is unauthorised will fire
          // onUnauthorized and sign out then.
          setUser(await cachedUser());
        }
      } finally {
        setBooting(false);
      }
    })();
  }, []);

  // Any request coming back 401 mid-session means the token stopped being
  // valid. Drop it here once rather than letting five screens each show
  // their own "unauthorized" error.
  useEffect(() => onUnauthorized(() => { signOut().catch(() => {}); }), [signOut]);

  const signIn = useCallback(async (email: string, password: string) => {
    const me = await apiLogin(email.trim().toLowerCase(), password);
    setUser(me);
    cacheUser(me);
    return me;
  }, []);

  const signUp = useCallback(async (email: string, password: string, name?: string) => {
    const me = await apiRegister(email.trim().toLowerCase(), password, name?.trim() || undefined);
    setUser(me);
    cacheUser(me);
    return me;
  }, []);

  const value = useMemo<AuthState>(
    () => ({ user, booting, signIn, signUp, signOut }),
    [user, booting, signIn, signUp, signOut],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth() {
  return useContext(Ctx);
}
