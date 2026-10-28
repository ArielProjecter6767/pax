import React, { useEffect, useRef, useState } from "react";
import {
  ActivityIndicator, Alert, FlatList, KeyboardAvoidingView, Platform,
  SafeAreaView, StatusBar, StyleSheet, Text, TextInput, TouchableOpacity, View
} from "react-native";
import AsyncStorage from "@react-native-async-storage/async-storage";

const API = (process.env.EXPO_PUBLIC_API_URL || "").replace(/\/$/, "");
const C = { bg: "#090B12", panel: "#111522", panel2: "#171D2D", line: "#252C40", text: "#F4F6FF", muted: "#8E98B2", accent: "#7C5CFF", green: "#39D9A4", bubble: "#232943" };

async function request(path, token, options = {}) {
  if (!API) throw new Error("Сначала укажи EXPO_PUBLIC_API_URL в настройках сборки Expo.");
  const res = await fetch(API + path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: "Bearer " + token } : {}), ...(options.headers || {}) },
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || "Ошибка сервера (" + res.status + ")");
  return data;
}

export default function App() {
  const [token, setToken] = useState("");
  const [me, setMe] = useState(null);
  const [screen, setScreen] = useState("auth");
  const [authMode, setAuthMode] = useState("register");
  const [login, setLogin] = useState("");
  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [users, setUsers] = useState([]);
  const [search, setSearch] = useState("");
  const [active, setActive] = useState(null);
  const [messages, setMessages] = useState([]);
  const [draft, setDraft] = useState("");
  const socket = useRef(null);
  const listRef = useRef(null);

  const logout = async () => {
    socket.current?.close();
    socket.current = null;
    await AsyncStorage.removeItem("pax_token");
    setToken(""); setMe(null); setActive(null); setScreen("auth");
  };

  const loadUsers = async (q = "") => {
    try { setUsers(await request("/api/users?q=" + encodeURIComponent(q), token)); }
    catch (e) { Alert.alert("PAX", e.message); }
  };

  const openChat = async (person) => {
    setActive(person);
    setScreen("chat");
    try { setMessages(await request("/api/messages/" + person.id, token)); }
    catch (e) { Alert.alert("PAX", e.message); }
  };

  const enterApp = async (newToken, user) => {
    await AsyncStorage.setItem("pax_token", newToken);
    setToken(newToken); setMe(user); setScreen("home");
    setBusy(false);
  };

  useEffect(() => {
    (async () => {
      try {
        const saved = await AsyncStorage.getItem("pax_token");
        if (saved) {
          const user = await request("/api/auth/me", saved);
          setToken(saved); setMe(user); setScreen("home");
        }
      } catch { await AsyncStorage.removeItem("pax_token"); }
    })();
  }, []);

  useEffect(() => {
    if (!token || !API) return;
    loadUsers();
    const wsUrl = API.replace(/^https:/, "wss:").replace(/^http:/, "ws:") + "/ws?token=" + encodeURIComponent(token);
    const ws = new WebSocket(wsUrl);
    socket.current = ws;
    ws.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        if (payload.type === "message" && payload.message) {
          const m = payload.message;
          if (active && (m.sender_id === active.id || m.recipient_id === active.id)) {
            setMessages(prev => prev.some(x => x.id === m.id) ? prev : [...prev, m]);
          }
        }
      } catch {}
    };
    return () => { ws.close(); if (socket.current === ws) socket.current = null; };
  }, [token, active?.id]);

  const submitAuth = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const body = authMode === "register"
        ? { username: username.trim(), display_name: (displayName.trim() || username.trim()), email: email.trim(), password }
        : { login: login.trim(), password };
      const data = await request(authMode === "register" ? "/api/auth/register" : "/api/auth/login", "", { method: "POST", body: JSON.stringify(body) });
      await enterApp(data.access_token, data.user);
    } catch (e) { setBusy(false); Alert.alert("Не удалось войти", e.message); }
  };

  const send = async () => {
    const text = draft.trim();
    if (!text || !active) return;
    setDraft("");
    try {
      const m = await request("/api/messages", token, { method: "POST", body: JSON.stringify({ recipient_id: active.id, text }) });
      setMessages(prev => prev.some(x => x.id === m.id) ? prev : [...prev, m]);
    } catch (e) { setDraft(text); Alert.alert("Сообщение не отправлено", e.message); }
  };

  const Header = ({ title, back }) => (
    <View style={s.header}>
      {back ? <TouchableOpacity onPress={back} style={s.back}><Text style={s.backText}>‹</Text></TouchableOpacity> : <View style={s.logo}><Text style={s.logoText}>P</Text></View>}
      <View style={{ flex: 1 }}><Text style={s.brand}>{title}</Text><Text style={s.sub}>{me ? "@" + me.username : "MESSAGES THAT MATTER"}</Text></View>
      {me && screen !== "auth" ? <TouchableOpacity onPress={() => Alert.alert("Аккаунт", me.username, [{ text: "Выйти", style: "destructive", onPress: logout }, { text: "Закрыть" }])}><Text style={s.more}>•••</Text></TouchableOpacity> : null}
    </View>
  );

  if (screen === "auth") return (
    <SafeAreaView style={s.root}><StatusBar barStyle="light-content" backgroundColor={C.bg} />
      <View style={s.authWrap}>
        <View style={s.heroMark}><Text style={s.heroP}>P</Text></View>
        <Text style={s.heroTitle}>PAX</Text><Text style={s.heroSub}>YOUR PEOPLE. YOUR SPACE.</Text>
        <View style={s.authCard}>
          <Text style={s.cardTitle}>{authMode === "register" ? "Создать аккаунт" : "С возвращением"}</Text>
          <Text style={s.label}>{authMode === "register" ? "Имя пользователя" : "Логин или email"}</Text>
          <TextInput style={s.input} value={authMode === "register" ? username : login} onChangeText={authMode === "register" ? setUsername : setLogin} autoCapitalize="none" placeholder={authMode === "register" ? "например, alex_01" : "username@example.com"} placeholderTextColor={C.muted} />
          {authMode === "register" ? <>
            <Text style={s.label}>Отображаемое имя</Text><TextInput style={s.input} value={displayName} onChangeText={setDisplayName} placeholder="Как вас называть" placeholderTextColor={C.muted} />
            <Text style={s.label}>Email</Text><TextInput style={s.input} value={email} onChangeText={setEmail} autoCapitalize="none" keyboardType="email-address" placeholder="you@example.com" placeholderTextColor={C.muted} />
          </> : null}
          <Text style={s.label}>Пароль (минимум 8 символов)</Text><TextInput style={s.input} value={password} onChangeText={setPassword} secureTextEntry placeholder="••••••••" placeholderTextColor={C.muted} />
          <TouchableOpacity style={[s.primary, busy && { opacity: 0.65 }]} onPress={submitAuth} disabled={busy}>
            {busy ? <ActivityIndicator color="white" /> : <Text style={s.primaryText}>{authMode === "register" ? "Создать аккаунт  →" : "Войти  →"}</Text>}
          </TouchableOpacity>
          <TouchableOpacity onPress={() => setAuthMode(authMode === "register" ? "login" : "register")} style={s.switchRow}>
            <Text style={s.muted}>{authMode === "register" ? "Уже есть аккаунт? " : "Впервые в PAX? "}</Text><Text style={s.link}>{authMode === "register" ? "Войти" : "Регистрация"}</Text>
          </TouchableOpacity>
        </View>
        <Text style={s.footnote}>Частные сообщения • Быстрая связь • PAX 0.1</Text>
      </View>
    </SafeAreaView>
  );

  if (screen === "chat" && active) return (
    <SafeAreaView style={s.root}><StatusBar barStyle="light-content" backgroundColor={C.bg} />
      <Header title={active.display_name || active.username} back={() => { setScreen("home"); setActive(null); }} />
      <View style={s.chatInfo}><Text style={s.chatHandle}>@{active.username}</Text><Text style={s.onlineDot}>●  PAX CHAT</Text></View>
      <FlatList ref={listRef} data={messages} keyExtractor={m => m.id} contentContainerStyle={s.messageList} onContentSizeChange={() => listRef.current?.scrollToEnd({ animated: true })} ListEmptyComponent={<Text style={s.empty}>Напишите первое сообщение 👋</Text>} renderItem={({ item }) => {
        const mine = item.sender_id === me?.id;
        return <View style={[s.messageRow, mine ? { justifyContent: "flex-end" } : { justifyContent: "flex-start" }]}><View style={[s.bubble, mine ? s.mineBubble : s.theirBubble]}><Text style={s.messageText}>{item.text}</Text><Text style={s.time}>{new Date(item.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</Text></View></View>;
      }} />
      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} keyboardVerticalOffset={8}>
        <View style={s.composer}><TextInput style={s.composerInput} value={draft} onChangeText={setDraft} placeholder="Написать сообщение..." placeholderTextColor={C.muted} multiline maxLength={4000} /><TouchableOpacity onPress={send} style={s.sendButton}><Text style={s.sendText}>↑</Text></TouchableOpacity></View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );

  return (
    <SafeAreaView style={s.root}><StatusBar barStyle="light-content" backgroundColor={C.bg} />
      <Header title="Сообщения" />
      <View style={s.welcome}><Text style={s.welcomeSmall}>ДОБРО ПОЖАЛОВАТЬ</Text><Text style={s.welcomeTitle}>Твои люди.{ "\n" }Твоё пространство.</Text><Text style={s.welcomeBody}>Найди человека и начни разговор. Сообщения сохраняются на сервере.</Text></View>
      <View style={s.searchBox}><Text style={s.searchIcon}>⌕</Text><TextInput style={s.searchInput} value={search} onChangeText={v => { setSearch(v); loadUsers(v); }} placeholder="Поиск по имени или @username" placeholderTextColor={C.muted} /></View>
      <View style={s.sectionHead}><Text style={s.sectionTitle}>ЛЮДИ</Text><Text style={s.count}>{users.length} пользователей</Text></View>
      <FlatList data={users} keyExtractor={u => u.id} contentContainerStyle={{ paddingBottom: 24 }} ListEmptyComponent={<Text style={s.empty}>Пока никого нет. Зарегистрируйте второй аккаунт, чтобы протестировать чат.</Text>} renderItem={({ item }) => (
        <TouchableOpacity style={s.userRow} onPress={() => openChat(item)}>
          <View style={s.avatar}><Text style={s.avatarText}>{(item.display_name || item.username).slice(0, 1).toUpperCase()}</Text></View>
          <View style={{ flex: 1 }}><Text style={s.userName}>{item.display_name || item.username}</Text><Text style={s.userHandle}>@{item.username}</Text></View>
          <Text style={s.chevron}>›</Text>
        </TouchableOpacity>
      )} />
      <View style={s.bottomBar}><Text style={s.bottomActive}>◉  Чаты</Text><Text style={s.bottomMuted}>◌  Звонки</Text><Text style={s.bottomMuted}>◎  Профиль</Text></View>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: C.bg },
  header: { height: 70, flexDirection: "row", alignItems: "center", paddingHorizontal: 20, borderBottomWidth: 1, borderBottomColor: C.line, gap: 12 },
  logo: { width: 42, height: 42, borderRadius: 14, backgroundColor: C.accent, alignItems: "center", justifyContent: "center" },
  logoText: { color: "#fff", fontSize: 26, fontWeight: "900" },
  brand: { color: C.text, fontSize: 18, fontWeight: "800", letterSpacing: 0.4 },
  sub: { color: C.muted, fontSize: 10, marginTop: 3, letterSpacing: 1.2 },
  more: { color: C.text, fontSize: 23, padding: 8 },
  back: { width: 34, height: 40, justifyContent: "center" },
  backText: { color: C.text, fontSize: 38 },
  authWrap: { flex: 1, justifyContent: "center", paddingHorizontal: 24, paddingVertical: 18 },
  heroMark: { width: 66, height: 66, borderRadius: 22, backgroundColor: C.accent, alignSelf: "center", alignItems: "center", justifyContent: "center", marginBottom: 12 },
  heroP: { color: "#fff", fontSize: 42, fontWeight: "900" },
  heroTitle: { color: C.text, fontSize: 38, fontWeight: "900", textAlign: "center", letterSpacing: 5 },
  heroSub: { color: C.muted, textAlign: "center", fontSize: 10, letterSpacing: 2, marginTop: 4, marginBottom: 24 },
  authCard: { backgroundColor: C.panel, borderRadius: 24, padding: 20, borderWidth: 1, borderColor: C.line },
  cardTitle: { color: C.text, fontSize: 21, fontWeight: "800", marginBottom: 18 },
  label: { color: C.muted, fontSize: 12, marginTop: 10, marginBottom: 7 },
  input: { height: 48, borderRadius: 12, backgroundColor: C.panel2, borderWidth: 1, borderColor: C.line, color: C.text, paddingHorizontal: 13 },
  primary: { height: 50, backgroundColor: C.accent, borderRadius: 13, alignItems: "center", justifyContent: "center", marginTop: 20 },
  primaryText: { color: "#fff", fontWeight: "800", fontSize: 15 },
  switchRow: { flexDirection: "row", justifyContent: "center", marginTop: 18, flexWrap: "wrap" },
  muted: { color: C.muted, fontSize: 13 }, link: { color: "#A998FF", fontWeight: "700", fontSize: 13 },
  footnote: { color: "#606B85", textAlign: "center", fontSize: 10, marginTop: 20, letterSpacing: 0.5 },
  welcome: { paddingHorizontal: 22, paddingTop: 25, paddingBottom: 22 },
  welcomeSmall: { color: "#A998FF", fontSize: 10, letterSpacing: 2, fontWeight: "800" },
  welcomeTitle: { color: C.text, fontSize: 31, fontWeight: "900", lineHeight: 38, marginTop: 10 },
  welcomeBody: { color: C.muted, fontSize: 13, lineHeight: 19, marginTop: 9, maxWidth: 310 },
  searchBox: { marginHorizontal: 20, marginBottom: 22, height: 48, backgroundColor: C.panel, borderRadius: 14, borderWidth: 1, borderColor: C.line, flexDirection: "row", alignItems: "center", paddingHorizontal: 12 },
  searchIcon: { color: C.muted, fontSize: 24, marginRight: 8 }, searchInput: { flex: 1, color: C.text, fontSize: 13 },
  sectionHead: { paddingHorizontal: 22, flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: 8 },
  sectionTitle: { color: C.muted, fontSize: 11, fontWeight: "800", letterSpacing: 1.5 }, count: { color: "#626E88", fontSize: 11 },
  userRow: { minHeight: 74, flexDirection: "row", alignItems: "center", paddingHorizontal: 20, gap: 12, borderBottomWidth: 1, borderBottomColor: "#171D2A" },
  avatar: { width: 46, height: 46, borderRadius: 16, backgroundColor: "#272344", alignItems: "center", justifyContent: "center" },
  avatarText: { color: "#C7BDFF", fontSize: 19, fontWeight: "800" }, userName: { color: C.text, fontWeight: "700", fontSize: 14 }, userHandle: { color: C.muted, fontSize: 12, marginTop: 4 }, chevron: { color: C.muted, fontSize: 28 },
  bottomBar: { height: 58, flexDirection: "row", justifyContent: "space-around", alignItems: "center", borderTopWidth: 1, borderTopColor: C.line, backgroundColor: C.panel },
  bottomActive: { color: "#B9ABFF", fontWeight: "800", fontSize: 12 }, bottomMuted: { color: C.muted, fontSize: 12 },
  chatInfo: { paddingHorizontal: 20, paddingVertical: 10, flexDirection: "row", justifyContent: "space-between", borderBottomWidth: 1, borderBottomColor: C.line },
  chatHandle: { color: C.muted, fontSize: 12 }, onlineDot: { color: C.green, fontSize: 10, fontWeight: "700" },
  messageList: { padding: 16, flexGrow: 1, justifyContent: "flex-end" }, messageRow: { flexDirection: "row", marginVertical: 5 },
  bubble: { maxWidth: "84%", paddingHorizontal: 14, paddingTop: 10, paddingBottom: 7, borderRadius: 17 },
  mineBubble: { backgroundColor: C.accent, borderBottomRightRadius: 5 }, theirBubble: { backgroundColor: C.bubble, borderBottomLeftRadius: 5 },
  messageText: { color: "#fff", fontSize: 15, lineHeight: 21 }, time: { color: "#D4CFFF", fontSize: 9, textAlign: "right", marginTop: 5 },
  empty: { color: C.muted, textAlign: "center", marginHorizontal: 30, marginVertical: 24, lineHeight: 20, fontSize: 13 },
  composer: { flexDirection: "row", alignItems: "flex-end", paddingHorizontal: 12, paddingVertical: 10, borderTopWidth: 1, borderTopColor: C.line, gap: 8 },
  composerInput: { flex: 1, minHeight: 45, maxHeight: 120, backgroundColor: C.panel, color: C.text, borderRadius: 15, paddingHorizontal: 14, paddingTop: 12, paddingBottom: 10, borderWidth: 1, borderColor: C.line },
  sendButton: { width: 45, height: 45, borderRadius: 15, backgroundColor: C.accent, alignItems: "center", justifyContent: "center" }, sendText: { color: "#fff", fontSize: 25, fontWeight: "700", marginTop: -3 }
});
