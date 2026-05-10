import { create } from 'zustand';

type Tab = 'status' | 'files' | 'history' | 'workers' | 'settings';
type ConnectionState = 'connecting' | 'live' | 'offline';

interface DashboardStats {
  totalFiles: number;
  activeAgents: number;
  safetyAlerts: number;
  batonCount: number;
}

interface DashboardState {
  // Navigation
  activeTab: Tab;
  setActiveTab: (tab: Tab) => void;

  // Connection
  connectionState: ConnectionState;
  setConnectionState: (state: ConnectionState) => void;

  // Stats (synced from SSE snapshot)
  stats: DashboardStats;
  setStats: (stats: Partial<DashboardStats>) => void;
}

export const useStore = create<DashboardState>((set) => ({
  activeTab: 'status',
  setActiveTab: (tab) => set({ activeTab: tab }),

  connectionState: 'connecting',
  setConnectionState: (state) => set({ connectionState: state }),

  stats: {
    totalFiles: 0,
    activeAgents: 0,
    safetyAlerts: 0,
    batonCount: 0,
  },
  setStats: (newStats) =>
    set((state) => ({
      stats: { ...state.stats, ...newStats },
    })),
}));
