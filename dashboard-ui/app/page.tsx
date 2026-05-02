"use client";

import { useEffect, useState, useMemo } from "react";
import {
  AreaChart, Area, BarChart, Bar, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid, Cell, PieChart, Pie,
  LineChart, Line
} from "recharts";
import { motion, AnimatePresence } from "framer-motion";
import { 
  Activity, Shield, Users, Zap, Database, Globe, 
  ArrowUpRight, ArrowDownRight, Terminal, Cpu, 
  TrendingUp, Wallet, CheckCircle2, AlertCircle, Clock,
  ChevronRight, ExternalLink, RefreshCcw, Layers
} from "lucide-react";
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const API = "";
const POLL_MS = 2000;
const G = "#14F195";
const P = "#9945FF";
const RED = "#f43f5e";
const BLUE = "#38bdf8";
const AMBER = "#f59e0b";

const TC: Record<number, string> = {
  0: "#64748b",
  1: "#14F195",
  2: "#38bdf8",
  3: "#9945FF",
  4: "#f59e0b",
  5: "#f43f5e"
};

const PID = "Aydqk82Wt1Cni6GQHTSJimtVskZ9PqvA6QyhtRjcRN3a";

function getModelLogoSrc(name: string): string | null {
  const n = name.toLowerCase();
  if (n.includes("gpt") || n.includes("openai")) return "/brands/openai.svg";
  if (n.includes("llama") || n.includes("meta")) return "https://cdn.simpleicons.org/meta";
  if (n.includes("claude") || n.includes("anthropic")) return "https://cdn.simpleicons.org/anthropic";
  if (n.includes("gemini") || n.includes("google")) return "https://cdn.simpleicons.org/googlegemini";
  if (n.includes("mistral")) return "https://cdn.simpleicons.org/mistral";
  if (n.includes("phi") || n.includes("microsoft")) return "/brands/microsoft.svg";
  if (n.includes("grok") || n.includes("x-ai") || n.includes("xai")) return "/brands/grok.svg";
  if (n.includes("deepseek")) return "https://cdn.simpleicons.org/deepseek";
  return null;
}

/* ---- Types ---- */
interface Economy { aggregate_safety: number; active_agents: number; total_balance: number; total_earned: number; contracts_completed: number; contracts_failed: number }
interface Agent { agent_id: string; model_name: string; strategy: string; current_tier: number; balance: number; total_earned: number; total_penalties: number; contracts_completed: number; contracts_failed: number; status: string; robustness: { cc: number; er: number; as_: number; ih: number } | null; solscan_url?: string }
interface Trade { round: number; agent: string; task_id: string; task_prompt: string; tier: string; domain: string; passed: boolean; reward: number; penalty: number; token_cost: number; latency_ms: number; output_preview: string; constraints_passed: string[]; constraints_failed: string[] }
interface Evt { timestamp: number; type: string; agent: string; message: string }

function getBackendWsUrl(): string {
  if (typeof window === "undefined") {
    return "ws://localhost:8000/ws";
  }

  const explicit = process.env.NEXT_PUBLIC_WS_BASE;
  if (explicit) {
    return explicit;
  }

  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.hostname}:8000/ws`;
}

/* ---- Hooks ---- */
function usePoll<T>(url: string, ms: number): T | null {
  const [d, setD] = useState<T | null>(null);

  useEffect(() => {
    let a = true;
    let ws: WebSocket | null = null;
    let retryId: ReturnType<typeof setTimeout> | null = null;

    const connectWs = () => {
      if (!a) return;
      ws = new WebSocket(getBackendWsUrl());

      ws.onmessage = (e) => {
        try {
          const v = JSON.parse(e.data);
          if (a) setD(prev => ({ ...prev, ...v }));
        } catch {}
      };

      ws.onerror = () => {
        ws?.close();
      };

      ws.onclose = () => {
        if (!a) return;
        retryId = setTimeout(connectWs, 1000);
      };
    };

    // 1. WebSocket for real-time "pushes"
    connectWs();

    // 2. Poll as fallback/refresh
    const p = () => {
      fetch(url).then(r => r.json()).then(v => {
        if (a) setD(v)
      }).catch(() => { })
    };
    p();
    const id = setInterval(p, ms);
    
    return () => { 
      a = false; 
      clearInterval(id);
      if (retryId) clearTimeout(retryId);
      ws?.close();
    }
  }, [url, ms]);
  return d
}

/* ---- Atoms ---- */
const GlassCard = ({ children, className, delay = 0 }: { children: React.ReactNode; className?: string; delay?: number }) => (
  <motion.div
    initial={{ opacity: 0, y: 20 }}
    animate={{ opacity: 1, y: 0 }}
    transition={{ duration: 0.5, delay }}
    className={cn("bento-card", className)}
  >
    {children}
  </motion.div>
);

const Badge = ({ children, variant = "default", className }: { children: React.ReactNode; variant?: "default" | "success" | "warning" | "error" | "info" | "purple"; className?: string }) => {
  const variants = {
    default: "bg-zinc-800 text-zinc-300 border-zinc-700",
    success: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
    warning: "bg-amber-500/10 text-amber-400 border-amber-500/20",
    error: "bg-rose-500/10 text-rose-400 border-rose-500/20",
    info: "bg-sky-500/10 text-sky-400 border-sky-500/20",
    purple: "bg-purple-500/10 text-purple-400 border-purple-500/20",
  };
  return (
    <span className={cn("px-2 py-0.5 rounded-full text-[10px] font-bold border", variants[variant], className)}>
      {children}
    </span>
  );
};

const TierBadge = ({ t }: { t: number }) => {
  const c = TC[t] || "#64748b";
  return (
    <span className="px-2 py-0.5 rounded-md text-[10px] font-black tracking-tighter" style={{ background: c + "20", color: c, border: `1px solid ${c}30` }}>
      TIER {t}
    </span>
  );
};

const RobustBar = ({ l, v }: { l: string; v: number }) => {
  const p = Math.round(v * 100);
  const c = v >= .65 ? G : v >= .4 ? AMBER : RED;
  return (
    <div className="flex items-center gap-2 group">
      <span className="w-5 text-[9px] font-bold text-zinc-400 group-hover:text-zinc-300 transition-colors">{l}</span>
      <div className="flex-1 h-1 bg-zinc-800 rounded-full overflow-hidden">
        <motion.div
          initial={{ width: 0 }}
          animate={{ width: `${p}%` }}
          className="h-full rounded-full"
          style={{ backgroundColor: c, boxShadow: `0 0 8px ${c}40` }}
        />
      </div>
      <span className="w-7 text-right text-[9px] font-mono text-zinc-400">{p}%</span>
    </div>
  );
};

const CustomTooltip = ({ active, payload, label }: any) => {
  if (active && payload && payload.length) {
    return (
      <div className="bg-zinc-900/90 backdrop-blur-md border border-white/10 p-3 rounded-xl shadow-2xl">
        <p className="text-[10px] text-zinc-400 mb-1 uppercase font-bold tracking-wider">Round {label}</p>
        {payload.map((p: any, i: number) => (
          <div key={i} className="flex items-center gap-2">
            <div className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: p.color || p.fill }} />
            <p className="text-xs font-bold text-white">
              {p.name}: <span className="font-mono text-zinc-300">{p.value.toFixed(p.value < 1 ? 4 : 2)}</span>
            </p>
          </div>
        ))}
      </div>
    );
  }
  return null;
};

/* ---- Components ---- */

function EventTicker({ events }: { events: Evt[] }) {
  const last = useMemo(() => events.slice(-6).reverse(), [events]);
  if (!last.length) return (
    <div className="flex flex-col items-center justify-center h-32 border-2 border-dashed border-white/5 rounded-2xl">
      <p className="text-[10px] font-black text-zinc-600 uppercase tracking-widest">Awaiting Protocol Signals...</p>
    </div>
  );

  const getStyle = (type: string) => {
    if (type.includes("BANKRUPTCY") || type.includes("DENIED") || type.includes("FAIL")) 
      return { bg: "bg-rose-500/10", border: "border-rose-500/50", text: "text-rose-400", glow: "shadow-[0_0_15px_rgba(244,63,94,0.3)]", icon: <AlertCircle size={14} className="animate-pulse" /> };
    if (type.includes("UPGRADE") || type.includes("ALLOWED")) 
      return { bg: "bg-emerald-500/10", border: "border-emerald-500/50", text: "text-emerald-400", glow: "shadow-[0_0_15px_rgba(16,185,129,0.3)]", icon: <ArrowUpRight size={14} /> };
    if (type.includes("BLOCKED") || type.includes("CIRCUMVENTION")) 
      return { bg: "bg-amber-500/10", border: "border-amber-500/50", text: "text-amber-400", glow: "shadow-[0_0_15px_rgba(245,158,11,0.3)]", icon: <Shield size={14} /> };
    return { bg: "bg-sky-500/10", border: "border-sky-500/50", text: "text-sky-400", glow: "shadow-[0_0_15px_rgba(56,189,248,0.3)]", icon: <Activity size={14} /> };
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-black text-white uppercase tracking-tighter flex items-center gap-2">
          <Terminal size={16} className="text-solana-purple" />
          Live Protocol Alerts
        </h3>
        <Badge variant="purple" className="animate-pulse">DEMO MODE</Badge>
      </div>
      <div className="space-y-2 max-h-[320px] overflow-y-auto pr-2 custom-scrollbar">
        <AnimatePresence mode="popLayout">
          {last.map((e, i) => {
            const s = getStyle(e.type);
            return (
              <motion.div
                key={`${e.timestamp}-${i}`}
                initial={{ opacity: 0, x: 20, scale: 0.95 }}
                animate={{ opacity: 1, x: 0, scale: 1 }}
                exit={{ opacity: 0, scale: 0.9 }}
                className={cn(
                  "relative flex flex-col gap-1 p-3 rounded-xl border transition-all duration-500",
                  s.bg, s.border, s.glow
                )}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className={s.text}>{s.icon}</span>
                    <span className={cn("text-[10px] font-black uppercase tracking-widest", s.text)}>
                      {e.type.replace(/_/g, " ")}
                    </span>
                  </div>
                  <span className="text-[9px] font-mono text-white/40">R{e.timestamp}</span>
                </div>
                <p className="text-[11px] text-white font-medium leading-relaxed">
                  <span className="opacity-50 font-mono mr-1">{e.agent}:</span>
                  {e.message}
                </p>
                {/* Visual Impact Accent */}
                <div className={cn("absolute left-0 top-1/4 bottom-1/4 w-0.5 rounded-full", s.text.replace("text-", "bg-"))} />
              </motion.div>
            );
          })}
        </AnimatePresence>
      </div>
    </div>
  );
}

function EconomyTab({ eco, ts, events }: { eco: Economy | null; ts: any; events: Evt[] }) {
  if (!eco) return (
    <div className="flex flex-col items-center justify-center h-[60vh] text-zinc-400 gap-4">
      <RefreshCcw className="animate-spin text-solana-purple" size={32} />
      <p className="text-sm font-medium animate-pulse">Initializing Economy Core...</p>
    </div>
  );

  const safetyData = (ts?.safety || []).map((v: number, i: number) => ({ r: i + 1, safety: v }));
  const balanceData = (ts?.balance || []).map((v: number, i: number) => ({ r: i + 1, balance: v }));
  const rewardData = (ts?.rewards || []).map((v: number, i: number) => ({ 
    r: i + 1, 
    reward: v, 
    penalty: ts?.penalties?.[i] || 0 
  }));

  return (
    <div className="space-y-6">
      {/* Top Stats Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <GlassCard className="flex flex-col justify-between group">
          <div className="flex items-start justify-between">
            <div className="p-2 rounded-xl bg-emerald-500/10 text-emerald-400">
              <Shield size={20} />
            </div>
            <div className="text-right">
              <span className="text-[10px] font-bold text-zinc-400 uppercase tracking-widest">Safety Index</span>
              <p className="text-2xl font-black text-emerald-400 leading-none mt-1">
                {(eco.aggregate_safety * 100).toFixed(1)}%
              </p>
            </div>
          </div>
          <div className="mt-4 h-1 bg-zinc-800 rounded-full overflow-hidden">
            <motion.div 
              initial={{ width: 0 }}
              animate={{ width: `${eco.aggregate_safety * 100}%` }}
              className="h-full bg-emerald-500 shadow-[0_0_12px_rgba(16,185,129,0.5)]"
            />
          </div>
        </GlassCard>

        <GlassCard delay={0.1} className="flex flex-col justify-between">
          <div className="flex items-start justify-between">
            <div className="p-2 rounded-xl bg-solana-purple/10 text-solana-purple">
              <Users size={20} />
            </div>
            <div className="text-right">
              <span className="text-[10px] font-bold text-zinc-400 uppercase tracking-widest">Active Agents</span>
              <p className="text-2xl font-black text-white leading-none mt-1">{eco.active_agents}</p>
            </div>
          </div>
          <div className="mt-4 flex items-center justify-between text-[10px]">
            <span className="text-zinc-400 font-bold uppercase">Utilization</span>
            <span className="text-emerald-400 font-mono">100%</span>
          </div>
        </GlassCard>

        <GlassCard delay={0.2} className="flex flex-col justify-between">
          <div className="flex items-start justify-between">
            <div className="p-2 rounded-xl bg-sky-500/10 text-sky-400">
              <Wallet size={20} />
            </div>
            <div className="text-right">
              <span className="text-[10px] font-bold text-zinc-400 uppercase tracking-widest">Total Value</span>
              <p className="text-2xl font-black text-white leading-none mt-1">{eco.total_balance.toFixed(2)}</p>
            </div>
          </div>
          <div className="mt-4 flex items-center justify-between text-[10px]">
            <span className="text-zinc-400 font-bold uppercase">SOL Circulating</span>
            <span className="text-sky-400 font-mono">DEVNET</span>
          </div>
        </GlassCard>

        <GlassCard delay={0.3} className="flex flex-col justify-between overflow-hidden">
          <div className="absolute top-0 right-0 w-24 h-24 bg-solana-green/5 blur-3xl rounded-full" />
          <div className="flex items-start justify-between relative z-10">
            <div className="p-2 rounded-xl bg-solana-green/10 text-solana-green">
              <TrendingUp size={20} />
            </div>
            <div className="text-right">
              <span className="text-[10px] font-bold text-zinc-400 uppercase tracking-widest">Cumulative Yield</span>
              <p className="text-2xl font-black text-solana-green leading-none mt-1">+{eco.total_earned.toFixed(3)}</p>
            </div>
          </div>
          <div className="mt-4 flex items-center justify-between text-[10px] relative z-10">
            <span className="text-zinc-400 font-bold uppercase">Net Profitability</span>
            <span className="text-solana-green font-mono font-bold">{(eco.total_earned / (eco.total_balance || 1) * 100).toFixed(1)}%</span>
          </div>
        </GlassCard>
      </div>

      {/* Main Charts Row */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <GlassCard delay={0.4} className="lg:col-span-2 min-h-[400px]">
          <div className="flex items-center justify-between mb-8">
            <div>
              <h3 className="text-sm font-black text-white uppercase tracking-tight flex items-center gap-2">
                <Activity size={16} className="text-solana-purple" />
                Economic Convergence
              </h3>
              <p className="text-[10px] text-zinc-400 font-bold uppercase tracking-widest mt-0.5">Safety & Robustness over time</p>
            </div>
            <div className="flex gap-4">
               <div className="flex items-center gap-1.5">
                <div className="w-2 h-2 rounded-full bg-solana-green" />
                <span className="text-[10px] font-bold text-zinc-300 uppercase">Safety</span>
              </div>
              <div className="flex items-center gap-1.5">
                <div className="w-2 h-2 rounded-full bg-solana-purple" />
                <span className="text-[10px] font-bold text-zinc-300 uppercase">Sol</span>
              </div>
            </div>
          </div>
          <div className="h-[300px] w-full">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={safetyData}>
                <defs>
                  <linearGradient id="colorSafety" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor={G} stopOpacity={0.3}/>
                    <stop offset="95%" stopColor={G} stopOpacity={0}/>
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" vertical={false} />
                <XAxis dataKey="r" axisLine={false} tickLine={false} tick={{fontSize: 9, fill: "#71717a"}} />
                <YAxis axisLine={false} tickLine={false} tick={{fontSize: 9, fill: "#71717a"}} domain={[0, 1]} />
                <Tooltip content={<CustomTooltip />} />
                <Area type="monotone" dataKey="safety" stroke={G} strokeWidth={3} fillOpacity={1} fill="url(#colorSafety)" name="Safety Index" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </GlassCard>

        <GlassCard delay={0.5} className="flex flex-col">
          <EventTicker events={events} />
          <div className="mt-auto pt-6 border-t border-white/5 space-y-4">
             <div className="flex items-center justify-between text-[10px]">
                <span className="text-zinc-400 font-bold uppercase">Settlement Status</span>
                <Badge variant="success">Finalized</Badge>
             </div>
             <div className="flex items-center justify-between">
                <div className="flex flex-col">
                  <span className="text-[10px] text-zinc-400 font-bold uppercase">Success Rate</span>
                  <span className="text-lg font-black text-white">
                    {((eco.contracts_completed / (eco.contracts_completed + eco.contracts_failed || 1)) * 100).toFixed(1)}%
                  </span>
                </div>
                <div className="h-10 w-10">
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie 
                        data={[
                          { name: 'OK', value: eco.contracts_completed, fill: G },
                          { name: 'FAIL', value: eco.contracts_failed, fill: RED }
                        ]} 
                        innerRadius={15} 
                        outerRadius={20} 
                        stroke="none"
                        dataKey="value"
                      />
                    </PieChart>
                  </ResponsiveContainer>
                </div>
             </div>
          </div>
        </GlassCard>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <GlassCard delay={0.6}>
           <h3 className="text-[10px] font-bold text-zinc-400 uppercase tracking-widest mb-6">Incentive Distribution</h3>
           <div className="h-[200px]">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={rewardData}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" vertical={false} />
                <XAxis dataKey="r" axisLine={false} tickLine={false} tick={{fontSize: 9, fill: "#71717a"}} />
                <YAxis axisLine={false} tickLine={false} tick={{fontSize: 9, fill: "#71717a"}} />
                <Tooltip content={<CustomTooltip />} />
                <Bar dataKey="reward" fill={G} radius={[4, 4, 0, 0]} name="Rewards" />
                <Bar dataKey="penalty" fill={RED} radius={[4, 4, 0, 0]} name="Penalties" />
              </BarChart>
            </ResponsiveContainer>
           </div>
        </GlassCard>
        <GlassCard delay={0.7}>
           <h3 className="text-[10px] font-bold text-zinc-400 uppercase tracking-widest mb-6">Solana Liquidity Flow</h3>
           <div className="h-[200px]">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={balanceData}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" vertical={false} />
                <XAxis dataKey="r" axisLine={false} tickLine={false} tick={{fontSize: 9, fill: "#71717a"}} />
                <YAxis axisLine={false} tickLine={false} tick={{fontSize: 9, fill: "#71717a"}} />
                <Tooltip content={<CustomTooltip />} />
                <Line type="stepAfter" dataKey="balance" stroke={P} strokeWidth={3} dot={false} name="Total Balance" />
              </LineChart>
            </ResponsiveContainer>
           </div>
        </GlassCard>
      </div>
    </div>
  );
}

const ModelLogo = ({ name, className }: { name: string; className?: string }) => {
  const src = getModelLogoSrc(name);
  const [imageFailed, setImageFailed] = useState(false);

  if (src && !imageFailed) {
    return (
      <img
        src={src}
        alt={name}
        className={cn(className, "object-contain opacity-95 group-hover:opacity-100 transition-opacity")}
        style={{ filter: "brightness(0) invert(1) drop-shadow(0 0 2px rgba(255,255,255,0.3))" }}
        onError={() => setImageFailed(true)}
      />
    );
  }

  return <Cpu className={cn(className, "text-zinc-300")} />;
};

function AgentsTab({ agents }: { agents: Agent[] }) {
  const [sort, setSort] = useState<"earned" | "tier" | "balance">("earned");
  const s = useMemo(() => [...agents].sort((a, b) => 
    sort === "tier" ? b.current_tier - a.current_tier || b.total_earned - a.total_earned : 
    sort === "balance" ? b.balance - a.balance : 
    b.total_earned - a.total_earned
  ), [agents, sort]);

  if (!agents.length) return (
    <div className="flex flex-col items-center justify-center h-64 text-zinc-400 gap-4">
      <Cpu className="animate-pulse" size={32} />
      <p>Awaiting Agent Registration...</p>
    </div>
  );

  return (
    <div className="space-y-6">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-black text-white flex items-center gap-2">
            <Users size={20} className="text-solana-purple" />
            Active Fleet
          </h2>
          <p className="text-xs text-zinc-300 font-medium uppercase tracking-widest mt-1">
            {agents.length} AGENTS DEPLOYED TO Devnet
          </p>
        </div>
        <div className="flex items-center gap-2 bg-zinc-900/50 p-1 rounded-xl border border-white/5">
          {(["earned", "tier", "balance"] as const).map(x => (
            <button 
              key={x} 
              onClick={() => setSort(x)} 
              className={cn(
                "px-4 py-1.5 text-[10px] font-black uppercase transition-all rounded-lg",
                sort === x ? "bg-solana-purple text-white shadow-lg shadow-purple-500/20" : "text-zinc-400 hover:text-zinc-300"
              )}
            >
              {x}
            </button>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-6">
        <AnimatePresence mode="popLayout">
          {s.map((a, i) => (
            <GlassCard key={a.agent_id} delay={i * 0.05} className="flex flex-col gap-6 group hover:ring-2 hover:ring-solana-purple/20 transition-all">
              <div className="flex items-start justify-between">
                <div className="flex items-center gap-3">
                  <div className="relative">
                    <div className="w-12 h-12 rounded-2xl bg-zinc-950 border border-white/10 flex items-center justify-center overflow-hidden p-2.5">
                       <ModelLogo name={a.model_name} className="w-full h-full" />
                    </div>
                    {a.status === "active" && (
                      <span className="absolute -top-1 -right-1 w-3 h-3 bg-solana-green rounded-full border-2 border-zinc-900 animate-pulse" />
                    )}
                  </div>
                  <div>
                    <h3 className="font-black text-white group-hover:text-solana-purple transition-colors">{a.model_name}</h3>
                    <div className="flex items-center gap-2 mt-0.5">
                       <span className="text-[10px] font-mono text-zinc-400 truncate w-20">{a.agent_id}</span>
                       <TierBadge t={a.current_tier} />
                    </div>
                  </div>
                </div>
                {a.solscan_url && (
                  <a href={a.solscan_url} target="_blank" rel="noopener noreferrer" className="p-2 rounded-lg bg-white/5 text-zinc-400 hover:text-white hover:bg-solana-purple/20 transition-all">
                    <ExternalLink size={14} />
                  </a>
                )}
              </div>

              <div className="grid grid-cols-2 gap-4 py-4 border-y border-white/5">
                 <div>
                    <span className="text-[9px] font-bold text-zinc-400 uppercase tracking-widest">Balance</span>
                    <p className="font-mono text-sm font-bold text-white leading-none mt-1">{a.balance.toFixed(4)} <span className="text-[10px] text-zinc-400">SOL</span></p>
                 </div>
                 <div className="text-right">
                    <span className="text-[9px] font-bold text-zinc-400 uppercase tracking-widest">Earnings</span>
                    <p className="font-mono text-sm font-bold text-solana-green leading-none mt-1">+{a.total_earned.toFixed(4)}</p>
                 </div>
              </div>

              {a.robustness && (
                <div className="space-y-2.5">
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-[9px] font-bold text-zinc-400 uppercase tracking-widest">Robustness Vector</span>
                    <span className="text-[9px] font-bold text-emerald-400">QUALIFIED</span>
                  </div>
                  <RobustBar l="CC" v={a.robustness.cc} />
                  <RobustBar l="ER" v={a.robustness.er} />
                  <RobustBar l="AS" v={a.robustness.as_} />
                  <RobustBar l="IH" v={a.robustness.ih} />
                </div>
              )}

              <div className="flex items-center justify-between pt-2">
                 <div className="flex items-center gap-1.5">
                    <CheckCircle2 size={12} className="text-emerald-500" />
                    <span className="text-xs font-bold text-emerald-500">{a.contracts_completed}</span>
                    <AlertCircle size={12} className="text-rose-500 ml-2" />
                    <span className="text-xs font-bold text-rose-500">{a.contracts_failed}</span>
                 </div>
                 <Badge variant="purple" className="capitalize">{a.strategy}</Badge>
              </div>
            </GlassCard>
          ))}
        </AnimatePresence>
      </div>
    </div>
  );
}

function TradesTab({ trades, events }: { trades: Trade[]; events: Evt[] }) {
  const [selectedTrade, setSelectedTrade] = useState<number | null>(null);
  const blocked = useMemo(() => events.filter(e => e.type === "CIRCUMVENTION_BLOCKED" || e.type === "UPGRADE_DENIED"), [events]);
  
  const items = useMemo(() => [
    ...[...trades].reverse().map(t => ({ kind: "trade" as const, data: t, time: t.round })),
    ...blocked.map(e => ({ kind: "blocked" as const, data: e, time: e.timestamp }))
  ].sort((a, b) => b.time - a.time), [trades, blocked]);

  if (!items.length) return (
    <div className="flex flex-col items-center justify-center h-64 text-zinc-400 gap-4">
      <Layers className="animate-pulse" size={32} />
      <p>Waiting for Economic Activity...</p>
    </div>
  );

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
        <GlassCard className="p-4 flex flex-col gap-1">
           <span className="text-[10px] font-bold text-zinc-400 uppercase">Trades</span>
           <span className="text-xl font-black text-white">{trades.length}</span>
        </GlassCard>
        <GlassCard className="p-4 flex flex-col gap-1 border-emerald-500/10">
           <span className="text-[10px] font-bold text-zinc-400 uppercase">Passed</span>
           <span className="text-xl font-black text-emerald-400">{trades.filter(t => t.passed).length}</span>
        </GlassCard>
        <GlassCard className="p-4 flex flex-col gap-1 border-rose-500/10">
           <span className="text-[10px] font-bold text-zinc-400 uppercase">Failed</span>
           <span className="text-xl font-black text-rose-400">{trades.filter(t => !t.passed).length}</span>
        </GlassCard>
        <GlassCard className="p-4 flex flex-col gap-1 border-amber-500/10">
           <span className="text-[10px] font-bold text-zinc-400 uppercase">Blocked</span>
           <span className="text-xl font-black text-amber-400">{blocked.length}</span>
        </GlassCard>
      </div>

      <div className="space-y-3">
        {items.map((item, i) => {
          if (item.kind === "blocked") {
            const e = item.data as Evt;
            return (
              <motion.div 
                key={`b-${i}`} 
                initial={{ opacity: 0, x: -20 }}
                animate={{ opacity: 1, x: 0 }}
                className="group p-4 bg-amber-500/5 border border-amber-500/10 rounded-2xl flex items-center justify-between gap-4"
              >
                <div className="flex items-center gap-4">
                   <div className="p-2 rounded-lg bg-amber-500/10 text-amber-400">
                      <Shield size={16} />
                   </div>
                   <div>
                      <div className="flex items-center gap-2">
                        <span className="text-[10px] font-bold text-amber-500/80 uppercase">Blocked</span>
                        <span className="text-xs font-bold text-white">{e.agent}</span>
                      </div>
                      <p className="text-[10px] text-zinc-400 mt-0.5">{e.message}</p>
                   </div>
                </div>
                <Badge variant="warning">Devnet Guard</Badge>
              </motion.div>
            );
          }

          const t = item.data as Trade;
          const isOpen = selectedTrade === i;

          return (
            <motion.div 
              key={`t-${i}`} 
              initial={{ opacity: 0, x: -20 }}
              animate={{ opacity: 1, x: 0 }}
              className={cn(
                "group rounded-2xl border transition-all cursor-pointer overflow-hidden",
                isOpen ? "bg-zinc-900 border-white/20" : "bg-zinc-900/40 border-white/5 hover:border-white/10 hover:bg-zinc-900/60"
              )}
              onClick={() => setSelectedTrade(isOpen ? null : i)}
            >
              <div className="p-4 flex items-center justify-between gap-4">
                 <div className="flex items-center gap-4 flex-1 overflow-hidden">
                    <div className={cn(
                      "p-2 rounded-lg",
                      t.passed ? "bg-emerald-500/10 text-emerald-400" : "bg-rose-500/10 text-rose-400"
                    )}>
                       {t.passed ? <CheckCircle2 size={16} /> : <AlertCircle size={16} />}
                    </div>
                    <div className="flex-1 min-w-0">
                       <div className="flex items-center gap-2">
                          <span className="text-xs font-black text-white">{t.agent}</span>
                          <TierBadge t={parseInt(t.tier.replace("T", "")) || 0} />
                          <span className="text-[9px] font-bold text-zinc-500 uppercase tracking-widest">{t.domain}</span>
                       </div>
                       <p className="text-[10px] text-zinc-300 mt-0.5 truncate">{t.task_id}</p>
                    </div>
                 </div>
                 <div className="text-right">
                    <p className={cn(
                      "font-mono text-xs font-black",
                      t.passed ? "text-emerald-400" : "text-rose-400"
                    )}>
                      {t.passed ? `+${t.reward.toFixed(4)}` : `-${t.penalty.toFixed(4)}`} SOL
                    </p>
                    <p className="text-[9px] text-zinc-300 font-bold uppercase mt-0.5">Round {t.round}</p>
                 </div>
              </div>

              <AnimatePresence>
                {isOpen && (
                  <motion.div 
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: "auto", opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                    className="border-t border-white/5 bg-black/40 p-6 space-y-6"
                  >
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-6">
                       <div>
                          <span className="text-[9px] font-bold text-zinc-500 uppercase tracking-widest">Network Cost</span>
                          <p className="text-xs font-mono text-zinc-300 mt-1">{t.token_cost.toFixed(6)} SOL</p>
                       </div>
                       <div>
                          <span className="text-[9px] font-bold text-zinc-500 uppercase tracking-widest">Latency</span>
                          <p className="text-xs font-mono text-zinc-300 mt-1">{t.latency_ms.toFixed(0)} ms</p>
                       </div>
                       <div className="col-span-2">
                          <span className="text-[9px] font-bold text-zinc-500 uppercase tracking-widest">Verification Status</span>
                          <div className="flex flex-wrap gap-1.5 mt-2">
                             {t.constraints_passed.map((c, j) => <Badge key={`p-${j}`} variant="success">{c}</Badge>)}
                             {t.constraints_failed.map((c, j) => <Badge key={`f-${j}`} variant="error">{c}</Badge>)}
                          </div>
                       </div>
                    </div>

                    <div>
                      <span className="text-[9px] font-bold text-zinc-500 uppercase tracking-widest">Job Specification</span>
                      <pre className="mt-2 p-4 rounded-xl bg-zinc-950 text-[10px] text-zinc-300 font-mono border border-white/5 whitespace-pre-wrap">
                        {t.task_prompt}
                      </pre>
                    </div>

                    <div>
                      <span className="text-[9px] font-bold text-zinc-500 uppercase tracking-widest">Agent Resolution</span>
                      <div className="mt-2 p-4 rounded-xl bg-zinc-950 text-[10px] text-zinc-300 font-mono border border-white/5 italic">
                        &quot;{t.output_preview}&quot;
                      </div>
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </motion.div>
          );
        })}
      </div>
    </div>
  );
}

function OnChainTab() {
  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <GlassCard className="overflow-hidden">
        <div className="absolute top-0 right-0 w-64 h-64 bg-solana-purple/10 blur-3xl rounded-full -mr-32 -mt-32" />
        <div className="relative z-10">
          <div className="flex items-center gap-4 mb-8">
            <div className="p-3 rounded-2xl bg-solana-purple/20 text-solana-purple">
               <Database size={24} />
            </div>
            <div>
               <h2 className="text-xl font-black text-white">Solana Devnet Registry</h2>
               <p className="text-xs text-zinc-400 font-bold uppercase tracking-widest">Protocol Version 1.0.4-LATEST</p>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
            <div className="space-y-6">
               <div>
                  <span className="text-[10px] font-bold text-zinc-400 uppercase tracking-widest">Program Authority</span>
                  <div className="flex items-center gap-2 mt-2">
                     <code className="text-xs text-solana-green font-mono bg-solana-green/5 px-3 py-1.5 rounded-lg border border-solana-green/20 break-all">{PID}</code>
                  </div>
               </div>
               <div>
                  <span className="text-[10px] font-bold text-zinc-400 uppercase tracking-widest">Core Instructions</span>
                  <div className="flex flex-wrap gap-2 mt-3">
                    {["initialize", "register_agent", "certify_agent", "create_contract", "accept_contract", "complete_contract", "fail_contract", "expire_contract"].map(ix => (
                      <span key={ix} className="px-3 py-1.5 rounded-lg bg-zinc-800 text-[10px] font-bold text-zinc-300 border border-white/5 hover:border-white/10 transition-colors uppercase">
                        {ix}
                      </span>
                    ))}
                  </div>
               </div>
            </div>

            <div className="space-y-4">
              <div className="p-4 rounded-2xl bg-zinc-900/50 border border-white/5 space-y-3">
                 <div className="flex items-center gap-2 text-white font-bold text-sm">
                    <Globe size={16} className="text-sky-400" />
                    Distributed Validation
                 </div>
                 <p className="text-xs text-zinc-300 leading-relaxed">
                    The Comprehension Gate uses an on-chain verification vector. Robustness scores (CC, ER, AS, IH) are stored in Agent PDAs and validated via threshold signatures before any contract is awarded.
                 </p>
              </div>
              <a 
                href={`https://solscan.io/account/${PID}?cluster=devnet`} 
                target="_blank" 
                rel="noopener noreferrer"
                className="w-full py-4 rounded-2xl bg-gradient-to-r from-solana-purple to-solana-green text-black font-black text-sm uppercase flex items-center justify-center gap-2 hover:opacity-90 transition-opacity"
              >
                Explore on Solscan <ExternalLink size={16} />
              </a>
            </div>
          </div>
        </div>
      </GlassCard>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
         {[
           { icon: <Shield className="text-emerald-400" />, title: "Theorem 1", desc: "Budget ceilings are enforced on-chain via PDA state isolation." },
           { icon: <Zap className="text-amber-400" />, title: "Theorem 2", desc: "Latency penalties are auto-deducted from escrow on completion." },
           { icon: <Activity className="text-solana-purple" />, title: "Theorem 3", desc: "Aggregate safety converges to the weakest-link gate value." }
         ].map((t, i) => (
           <GlassCard key={i} className="p-6">
              <div className="p-2 rounded-xl bg-white/5 w-fit mb-4">
                 {t.icon}
              </div>
              <h4 className="text-white font-black text-sm uppercase tracking-tighter mb-2">{t.title}</h4>
              <p className="text-xs text-zinc-400 leading-relaxed">{t.desc}</p>
           </GlassCard>
         ))}
      </div>
    </div>
  );
}

/* ---- Main Dashboard ---- */

export default function Dashboard() {
  const [tab, setTab] = useState<"economy" | "agents" | "trades" | "onchain">("economy");
  const st = usePoll<{ status: string; round: number; total_rounds: number; economy: Economy | null }>(`${API}/api/state`, POLL_MS);
  const ag = usePoll<{ agents: Agent[] }>(`${API}/api/agents`, POLL_MS);
  const tr = usePoll<{ trades: Trade[] }>(`${API}/api/trades?limit=200`, POLL_MS);
  const ts = usePoll<any>(`${API}/api/timeseries`, POLL_MS);
  const ev = usePoll<{ events: Evt[] }>(`${API}/api/events?limit=200`, POLL_MS);

  const status = st?.status || "connecting";
  const round = st?.round || 0;
  const totalR = st?.total_rounds || 0;

  const tabs = [
    { id: "economy" as const, label: "Economy", icon: <Activity size={14} /> },
    { id: "agents" as const, label: "Agents", icon: <Users size={14} />, count: ag?.agents?.length },
    { id: "trades" as const, label: "Trades", icon: <RefreshCcw size={14} />, count: tr?.trades?.length },
    { id: "onchain" as const, label: "On-Chain", icon: <Database size={14} /> },
  ];

  return (
    <div className="min-h-screen bg-black text-zinc-100 font-sans selection:bg-solana-purple/30 pb-20">
      {/* Background Decor */}
      <div className="fixed inset-0 pointer-events-none overflow-hidden">
        <div className="absolute top-[-10%] left-[-10%] w-[40%] h-[40%] bg-solana-purple/5 blur-[120px] rounded-full animate-float" />
        <div className="absolute bottom-[-10%] right-[-10%] w-[40%] h-[40%] bg-solana-green/5 blur-[120px] rounded-full animate-float" style={{ animationDelay: "-3s" }} />
      </div>

      <header className="sticky top-0 z-50 bg-black/60 backdrop-blur-xl border-b border-white/5 px-6 py-4">
        <div className="max-w-7xl mx-auto flex items-center justify-between">
          <div className="flex items-center gap-6">
            <motion.div 
              whileHover={{ rotate: 90 }}
              className="w-10 h-10 rounded-2xl bg-gradient-to-br from-solana-purple to-solana-green p-0.5"
            >
              <div className="w-full h-full bg-black rounded-[14px] flex items-center justify-center">
                <div className="w-5 h-5 bg-gradient-to-br from-solana-purple to-solana-green rounded-md" />
              </div>
            </motion.div>
            <div className="flex flex-col">
              <h1 className="text-lg font-black tracking-tighter text-white leading-none">CGAE</h1>
              <p className="text-[10px] text-zinc-400 font-bold uppercase tracking-[0.2em] mt-1">Comprehension Gated Agent Economy</p>
            </div>
            <div className="hidden md:flex items-center gap-4 ml-6 pl-6 border-l border-white/10">
              <div className="flex items-center gap-2">
                <div className={cn(
                  "w-2 h-2 rounded-full",
                  status === "running" ? "bg-emerald-500 animate-pulse shadow-[0_0_8px_#10b981]" : "bg-zinc-600"
                )} />
                <span className="text-[10px] font-black uppercase tracking-widest text-zinc-300">{status}</span>
              </div>
              {round > 0 && (
                <div className="text-[10px] font-black uppercase tracking-widest text-zinc-400">
                  Round <span className="text-white">{round}</span>
                  {totalR > 0 && <span className="text-zinc-500">/{totalR}</span>}
                </div>
              )}
            </div>
          </div>
          
          <div className="flex items-center gap-3">
             <a 
              href={`https://solscan.io/account/${PID}?cluster=devnet`} 
              target="_blank" 
              rel="noopener noreferrer"
              className="hidden sm:flex items-center gap-2 px-4 py-2 rounded-xl bg-zinc-900 border border-white/10 text-[10px] font-bold text-zinc-200 hover:bg-zinc-800 hover:border-white/20 transition-all"
             >
              <ExternalLink size={12} /> Solscan
             </a>
             <div className="px-4 py-2 rounded-xl bg-emerald-500/10 border border-emerald-500/20 text-[10px] font-black text-emerald-400 uppercase tracking-widest">
                Devnet Active
             </div>
          </div>
        </div>
      </header>

      <div className="max-w-7xl mx-auto px-6">
        <nav className="flex items-center gap-1 mt-8 p-1 bg-zinc-900/50 backdrop-blur-md rounded-2xl border border-white/5 w-fit">
          {tabs.map(t => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={cn(
                "relative px-6 py-2.5 text-[11px] font-black uppercase tracking-widest flex items-center gap-2 rounded-xl transition-all",
                tab === t.id ? "text-white" : "text-zinc-400 hover:text-zinc-300"
              )}
            >
              {tab === t.id && (
                <motion.div 
                  layoutId="tab-bg"
                  className="absolute inset-0 bg-zinc-800 rounded-xl border border-white/10 shadow-xl"
                  transition={{ type: "spring", bounce: 0.2, duration: 0.6 }}
                />
              )}
              <span className="relative z-10">{t.icon}</span>
              <span className="relative z-10">{t.label}</span>
              {t.count !== undefined && (
                <span className="relative z-10 ml-1 opacity-40">({t.count})</span>
              )}
            </button>
          ))}
        </nav>

        <main className="mt-8">
          <motion.div
            key={tab}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.4 }}
          >
            {tab === "economy" && <EconomyTab eco={st?.economy || null} ts={ts} events={ev?.events || []} />}
            {tab === "agents" && <AgentsTab agents={ag?.agents || []} />}
            {tab === "trades" && <TradesTab trades={tr?.trades || []} events={ev?.events || []} />}
            {tab === "onchain" && <OnChainTab />}
          </motion.div>
        </main>
      </div>

      <footer className="mt-32 px-6 py-12 border-t border-white/5">
        <div className="max-w-7xl mx-auto flex flex-col md:flex-row items-center justify-between gap-8">
           <div className="flex flex-col items-center md:items-start">
              <div className="flex items-center gap-2 mb-2">
                <div className="w-4 h-4 rounded bg-gradient-to-br from-solana-purple to-solana-green" />
                <span className="text-sm font-black text-white">CGAE CORE</span>
              </div>
              <p className="text-[10px] text-zinc-400 font-bold uppercase tracking-widest">
                Comprehension-Gated Agent Economy · Solana Frontier 2026
              </p>
           </div>
           <div className="flex items-center gap-8">
              <div className="flex flex-col text-center md:text-right">
                 <span className="text-[9px] font-bold text-zinc-600 uppercase tracking-widest">Powered By</span>
                 <span className="text-xs font-black text-zinc-300">SOLANA HIGH PERFORMANCE</span>
              </div>
              <div className="flex flex-col text-center md:text-right">
                 <span className="text-[9px] font-bold text-zinc-600 uppercase tracking-widest">Verification</span>
                 <span className="text-xs font-black text-zinc-300">PROBABILISTIC PROOFS</span>
              </div>
           </div>
        </div>
      </footer>
    </div>
  );
}
