import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type { Character, FoundVideo, ProviderStatus, Rating, RunDetail, RunSummary, StartRun } from "./types";

export const useCharacters = () =>
  useQuery({ queryKey: ["characters"], queryFn: () => api.get<Character[]>("/api/characters") });

export const useProviders = () =>
  useQuery({ queryKey: ["providers"], queryFn: () => api.get<ProviderStatus[]>("/api/providers"), refetchInterval: 60_000 });

export const useActiveRun = () =>
  useQuery({ queryKey: ["run", "active"], queryFn: () => api.get<RunDetail | null>("/api/runs/active") });

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

export function useRateVideo(runId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { clusterId: string; rating: Rating | null; note: string | null }) =>
      api.put(`/api/videos/${v.clusterId}/feedback`, { rating: v.rating, note: v.note }),
    onMutate: async (v) => {
      await qc.cancelQueries({ queryKey: ["videos", runId] });
      const before = qc.getQueryData<FoundVideo[]>(["videos", runId]);
      qc.setQueryData<FoundVideo[]>(["videos", runId], (list) => list?.map((x) => x.cluster_id === v.clusterId
        ? { ...x, feedback: v.rating ? { rating: v.rating, note: v.note } : null } : x));
      return { before };
    },
    onError: (_e, _v, c) => qc.setQueryData(["videos", runId], c?.before),
  });
}

export function useScoreRun(slug: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { runId: string; satisfaction: number; note: string | null }) =>
      api.put(`/api/runs/${v.runId}/feedback`, { satisfaction: v.satisfaction, note: v.note }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["runs", slug] }),
  });
}
