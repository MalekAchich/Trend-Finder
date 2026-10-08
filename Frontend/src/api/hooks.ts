import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type { AccountItem, Character, ManualVideo, CharacterDetail, FoundVideo, ModelChoice, ProviderPriority, ProviderStatus, ProviderUsage, Rating, RunDetail,
  RunHistoryRow, RunSummary, StartRun } from "./types";

export const useCharacters = () =>
  useQuery({ queryKey: ["characters"], queryFn: () => api.get<Character[]>("/api/characters") });

export const useProviders = () =>
  useQuery({ queryKey: ["providers"], queryFn: () => api.get<ProviderStatus[]>("/api/providers"), refetchInterval: 60_000 });

export const useActiveRun = () =>
  useQuery({ queryKey: ["run", "active"], queryFn: () => api.get<RunDetail | null>("/api/runs/active"),
    refetchInterval: 15_000 });

export const useRunsHistory = (character: string | null) =>
  useQuery({ queryKey: ["runs-history", character], refetchInterval: 20_000,
    queryFn: () => api.get<RunHistoryRow[]>(`/api/runs${character ? `?character=${encodeURIComponent(character)}` : ""}`) });

export const useCharacter = (slug: string | null) =>
  useQuery({ queryKey: ["character", slug], queryFn: () => api.get<CharacterDetail>(`/api/characters/${slug}`),
    enabled: !!slug });

export const useCharacterVideos = (slug: string | null, days: number | null) =>
  useQuery({ queryKey: ["character-videos", slug, days], enabled: !!slug,
    queryFn: () => api.get<FoundVideo[]>(`/api/characters/${slug}/videos${days ? `?days=${days}` : ""}`) });

export const useUsage = () =>
  useQuery({ queryKey: ["usage"], queryFn: () => api.get<{ providers: ProviderUsage[] }>("/api/usage"),
    refetchInterval: 20_000 });

export const useModelSettings = () =>
  useQuery({ queryKey: ["model-settings"], queryFn: () => api.get<Record<string, ModelChoice>>("/api/settings/models") });

export function useSaveModels() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { provider: string; main: string; fast: string; effort: string | null }) =>
      api.put<ModelChoice>("/api/settings/models", v),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["model-settings"] }),
  });
}

export const useBar = () =>
  useQuery({ queryKey: ["bar"], queryFn: () => api.get<{ min_score: number }>("/api/settings/bar") });

export function useSaveBar() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (min_score: number) => api.put<{ min_score: number }>("/api/settings/bar", { min_score }),
    onSuccess: (data) => qc.setQueryData(["bar"], data),
  });
}

export const usePriority = () =>
  useQuery({ queryKey: ["priority"], queryFn: () => api.get<ProviderPriority>("/api/settings/priority") });

export function useSavePriority() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (first: string | null) => api.put<ProviderPriority>("/api/settings/priority", { first }),
    onSuccess: (data) => qc.setQueryData(["priority"], data),
  });
}

export const useAccounts = () =>
  useQuery({
    queryKey: ["accounts"], queryFn: () => api.get<{ items: AccountItem[] }>("/api/settings/accounts"),
    // poll while a login window is open, so the card flips to "connected" by itself
    refetchInterval: (q) => q.state.data?.items.some((i) => i.connecting?.state === "waiting") ? 1500 : false,
  });

export function useAccountAction() {
  const qc = useQueryClient();
  const done = () => qc.invalidateQueries({ queryKey: ["accounts"] });
  return {
    setKey: useMutation({ mutationFn: (v: { id: string; value: string }) =>
      api.put<AccountItem>(`/api/settings/accounts/${v.id}`, { value: v.value }), onSuccess: done }),
    remove: useMutation({ mutationFn: (id: string) => api.del<AccountItem>(`/api/settings/accounts/${id}`),
      onSuccess: done }),
    connect: useMutation({ mutationFn: (id: string) => api.post(`/api/settings/accounts/${id}/connect`, {}),
      onSuccess: done }),
  };
}

export const useManualVideos = (character: string | null) =>
  useQuery({
    queryKey: ["manual-videos", character],
    queryFn: () => api.get<{ items: ManualVideo[] }>(`/api/manual-videos${character ? `?character=${character}` : ""}`),
    // while links are being looked up, refresh so their cards fill in by themselves
    refetchInterval: (q) => q.state.data?.items.some((v) => v.status === "checking") ? 2000 : false,
  });

export function useManualActions() {
  const qc = useQueryClient();
  const done = () => qc.invalidateQueries({ queryKey: ["manual-videos"] });
  return {
    add: useMutation({
      mutationFn: (v: { urls: string[]; reference: boolean; target: string | null }) =>
        api.post<{ added: number }>("/api/manual-videos", v), onSuccess: done }),
    setRoles: useMutation({
      mutationFn: (v: { id: string; is_reference?: boolean; target?: string | null }) => {
        const { id, ...body } = v;
        return api.patch(`/api/manual-videos/${id}`, body);
      }, onSuccess: done }),
    recheck: useMutation({ mutationFn: (id: string) => api.post(`/api/manual-videos/${id}/check`, {}), onSuccess: done }),
    remove: useMutation({ mutationFn: (id: string) => api.del(`/api/manual-videos/${id}`), onSuccess: done }),
  };
}

/** One rating mutation for every list a video can appear in (a run's grid, a character's videos). */
export function useRate() {
  const qc = useQueryClient();
  const lists = { predicate: (q: { queryKey: readonly unknown[] }) => ["videos", "character-videos"].includes(String(q.queryKey[0])) };
  return useMutation({
    mutationFn: (v: { clusterId: string; rating: Rating | null; note: string | null }) =>
      api.put(`/api/videos/${v.clusterId}/feedback`, { rating: v.rating, note: v.note }),
    onMutate: async (v) => {
      await qc.cancelQueries(lists);
      const before = qc.getQueriesData<FoundVideo[]>(lists);
      qc.setQueriesData<FoundVideo[]>(lists, (list) => list?.map((x) => x.cluster_id === v.clusterId
        ? { ...x, feedback: v.rating ? { rating: v.rating, note: v.note } : null } : x));
      return { before };
    },
    onError: (_e, _v, c) => c?.before.forEach(([key, data]) => qc.setQueryData(key, data)),
  });
}

export const useRun = (id: string | null) =>
  useQuery({ queryKey: ["run", id], queryFn: () => api.get<RunDetail>(`/api/runs/${id}`), enabled: !!id,
    refetchInterval: (q) => (q.state.data && !q.state.data.active ? false : 10_000) });

export const useCharacterRuns = (slug: string | null) =>
  useQuery({ queryKey: ["runs", slug], queryFn: () => api.get<RunSummary[]>(`/api/characters/${slug}/runs`),
    enabled: !!slug });

export const useRunVideos = (runId: string, live: boolean) =>
  useQuery({ queryKey: ["videos", runId], queryFn: () => api.get<FoundVideo[]>(`/api/runs/${runId}/videos`),
    refetchInterval: live ? 15_000 : false });

export function useStartRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: StartRun) => api.post<{ run_id: string }>("/api/runs", body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["run", "active"] }); qc.invalidateQueries({ queryKey: ["runs"] }); },
  });
}

export function useStopRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post(`/api/runs/${id}/stop`),
    onSuccess: (_d, id) => qc.invalidateQueries({ queryKey: ["run", id] }),
  });
}

export function useScoreRun(slug: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { runId: string; satisfaction: number; note: string | null }) =>
      api.put(`/api/runs/${v.runId}/feedback`, { satisfaction: v.satisfaction, note: v.note }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["runs", slug] }); qc.invalidateQueries({ queryKey: ["runs-history"] }); },
  });
}
