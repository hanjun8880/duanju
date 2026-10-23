import { create } from "zustand";

export type Tab = "source" | "script" | "lookdev" | "storyboard" | "final";

interface WorkbenchState {
  projectId: string | null;
  tab: Tab;
  ledgerOpen: boolean;
  chain: boolean;
  episode: number;
  setEpisode: (n: number) => void;
  setProject: (id: string | null) => void;
  setTab: (t: Tab) => void;
  setLedgerOpen: (open: boolean) => void;
  setChain: (c: boolean) => void;
}

export const useWorkbench = create<WorkbenchState>((set) => ({
  projectId: null,
  tab: "source",
  ledgerOpen: false,
  chain: true,
  episode: 1,
  setEpisode: (episode) => set({ episode }),
  setProject: (projectId) => set({ projectId }),
  setTab: (tab) => set({ tab }),
  setLedgerOpen: (ledgerOpen) => set({ ledgerOpen }),
  setChain: (chain) => set({ chain }),
}));
