variable "aws_region" {
  description = "The aws default region to deploy"
  type        = string
  default     = "us-east-1"
}

variable "target_bucket_name" {
  description = "S3 bucket for the raw zone. No default on purpose: set it with the TF_VAR_target_bucket_name environment variable"
  type        = string
  nullable    = false
}

variable "api_soccer_api_key" {
  description = "API key for the API Soccer service"
  type        = string
  sensitive   = true
  #just for testing purposes, in real life you would use Secrets Manager
  default = "prod/soccer/api"
}

variable "api_max_pages" {
  description = "Max pages fetched per paginated API-Sports request (free plan rejects page > 3). Empty string means no cap (paid plans)."
  type        = string
  default     = ""
}

variable "fixture_lookback_days" {
  description = "Regular runs re-detail finished fixtures played in the last N days (overlap catches late stat corrections)"
  type        = number
  default     = 10
}

variable "min_quota" {
  description = "Weekly run fails fast when fewer daily API-Sports requests than this remain (a full weekly run uses ~225)"
  type        = number
  default     = 300
}

variable "competitions" {
  description = <<EOT
Competitions ingested by the weekly/monthly workflows.
type: "league" (player season stats + squads per team) or "cup" (fixtures only).
season: the API-Sports season key. European competitions use the start year (2026 = 2026/27, bump in July);
Colombia (239, 241) uses the calendar year (bump in January). The monthly run reports each league's current_season.
EOT
  type = list(object({
    league        = number
    name          = string
    type          = string
    has_standings = bool
    season        = number
  }))
  default = [
    { league = 39, name = "Premier League", type = "league", has_standings = true, season = 2026 },
    { league = 140, name = "La Liga", type = "league", has_standings = true, season = 2026 },
    { league = 135, name = "Serie A", type = "league", has_standings = true, season = 2026 },
    { league = 78, name = "Bundesliga", type = "league", has_standings = true, season = 2026 },
    { league = 239, name = "Primera A", type = "league", has_standings = true, season = 2026 },
    { league = 2, name = "Champions League", type = "cup", has_standings = true, season = 2026 },
    { league = 45, name = "FA Cup", type = "cup", has_standings = false, season = 2026 },
    { league = 143, name = "Copa del Rey", type = "cup", has_standings = false, season = 2026 },
    { league = 137, name = "Coppa Italia", type = "cup", has_standings = false, season = 2026 },
    { league = 81, name = "DFB Pokal", type = "cup", has_standings = false, season = 2026 },
    { league = 241, name = "Copa Colombia", type = "cup", has_standings = false, season = 2026 },
  ]

  validation {
    condition     = alltrue([for c in var.competitions : contains(["league", "cup"], c.type)])
    error_message = "competitions[*].type must be \"league\" or \"cup\"."
  }
}

variable "lambda_layer_with_pandas" {
  description = "managed layer that includes pandas and requests library (the Lambda only needs requests from it)"
  type        = string
  default     = "arn:aws:lambda:us-east-1:336392948345:layer:AWSSDKPandas-Python312:12"

}
