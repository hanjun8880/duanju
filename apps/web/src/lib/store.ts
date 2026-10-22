import { create } from "zustand";

export type Tab = "import" | "lookdev" | "storyboard";

interface WorkbenchState {
  projectId: string | null;
  tab: Tab;
  ledgerOpen: boolean;
  chain: boolean;
  setProject: (id: string | null) => void;
  setTab: (t: Tab) => void;
  setLedgerOpen: (open: boolean) => void;
  setChain: (c: boolean) => void;
}

export const useWorkbench = create<WorkbenchState>((set) => ({
  projectId: null,
  tab: "import",
  ledgerOpen: false,
  chain: true,
  setProject: (projectId) => set({ projectId }),
  setTab: (tab) => set({ tab }),
  setLedgerOpen: (ledgerOpen) => set({ ledgerOpen }),
  setChain: (chain) => set({ chain }),
}));
