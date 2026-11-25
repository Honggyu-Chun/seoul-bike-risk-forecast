export interface Prediction {
  station_id: string;
  horizon_hours: 1 | 2 | 3;
  target_at: string;
  risk_probability: number | null;
  available_probability: number | null;
  is_risk: boolean | null;
  model_id: string | null;
  reason: string | null;
  input_group: string | null;
  latest_inventory: number | null;
  latest_inventory_received_at: string | null;
  latest_inventory_age_seconds: number | null;
  latest_inventory_observed_at: string | null;
}
export interface PredictionResponse {
  base_at: string;
  requested_at: string;
  items: Prediction[];
}
export interface RankedPrediction extends Prediction {
  name: string; latitude: number; longitude: number;
  distance_m: number; score: number;
}
export interface RecommendationResponse {
  base_at: string; requested_at: string; horizon_hours: 1 | 2 | 3;
  radius_m: number; distance_type: "straight_line";
  automatic_radius_expansion: false;
  recommended: RankedPrediction | null;
  alternatives: RankedPrediction[];
  abstained: boolean; reason: string | null;
  nearby_count: number; eligible_count: number;
}
// This URL is YOUR TEAM BACKEND, not the private AI service.
// Add team user-session authentication as agreed with the backend.
// The private AI bearer token must never be included in browser code.
export async function getPredictions(
  teamBackendUrl: string, stationIds: string[], horizons: Array<1 | 2 | 3> = [1, 2, 3]
): Promise<PredictionResponse> {
  const response = await fetch(teamBackendUrl + "/v1/predictions", {
    method: "POST", headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({ station_ids: stationIds, horizons_hours: horizons }),
    signal: AbortSignal.timeout(10000)
  });
  if (!response.ok) throw new Error("prediction_request_failed_" + response.status);
  return await response.json() as PredictionResponse;
}
