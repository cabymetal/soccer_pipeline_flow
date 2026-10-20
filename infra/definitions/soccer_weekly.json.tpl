{
    "Comment": "Weekly soccer ingestion: quota pre-flight, then per competition-season fixtures + match details, standings and player season stats. Input (all optional): {\"fixtures_only\": bool, \"mode\": \"weekly\"|\"backfill\", \"seasons\": [yyyy], \"competitions\": [...]}",
    "StartAt": "Set Defaults",
    "States": {
        "Set Defaults": {
            "Type": "Pass",
            "Parameters": {
                "defaults": {
                    "mode": "weekly",
                    "fixtures_only": false,
                    "seasons": null,
                    "competitions": ${competitions},
                    "min_quota": ${min_quota}
                },
                "input.$": "$"
            },
            "Next": "Merge Input"
        },
        "Merge Input": {
            "Type": "Pass",
            "Comment": "Execution input overrides the defaults; every invocation of the run shares run_id/run_date (ingest_date partition)",
            "Parameters": {
                "config.$": "States.JsonMerge($.defaults, $.input, false)",
                "run_id.$": "$$.Execution.Name",
                "run_date.$": "States.ArrayGetItem(States.StringSplit($$.Execution.StartTime, 'T'), 0)"
            },
            "Next": "Check Quota"
        },
        "Check Quota": {
            "Type": "Task",
            "Comment": "GET /status does not count against the daily quota",
            "Resource": "arn:aws:states:::lambda:invoke",
            "Parameters": {
                "FunctionName": "${lambda_soccer_arn}",
                "Payload": {
                    "action": "get_status",
                    "run_id.$": "$.run_id",
                    "run_date.$": "$.run_date"
                }
            },
            "ResultSelector": {
                "requests_remaining.$": "$.Payload.result.requests_remaining",
                "requests_limit_day.$": "$.Payload.result.requests_limit_day"
            },
            "ResultPath": "$.status",
            "Retry": [
                {
                    "ErrorEquals": ["Lambda.ServiceException", "Lambda.AWSLambdaException", "Lambda.SdkClientException", "Lambda.TooManyRequestsException"],
                    "IntervalSeconds": 2,
                    "MaxAttempts": 3,
                    "BackoffRate": 2
                }
            ],
            "Next": "Enough Quota?"
        },
        "Enough Quota?": {
            "Type": "Choice",
            "Choices": [
                {
                    "Variable": "$.status.requests_remaining",
                    "NumericLessThanPath": "$.config.min_quota",
                    "Next": "Quota Too Low"
                }
            ],
            "Default": "Map Over Competitions"
        },
        "Quota Too Low": {
            "Type": "Fail",
            "Error": "QuotaTooLow",
            "Cause": "Remaining daily API-Sports requests are below min_quota; run skipped"
        },
        "Map Over Competitions": {
            "Type": "Map",
            "MaxConcurrency": 3,
            "ItemsPath": "$.config.competitions",
            "ItemSelector": {
                "competition.$": "$$.Map.Item.Value",
                "seasons.$": "$.config.seasons",
                "mode.$": "$.config.mode",
                "fixtures_only.$": "$.config.fixtures_only",
                "run_id.$": "$.run_id",
                "run_date.$": "$.run_date"
            },
            "ItemProcessor": {
                "ProcessorConfig": { "Mode": "INLINE" },
                "StartAt": "Seasons Given?",
                "States": {
                    "Seasons Given?": {
                        "Type": "Choice",
                        "Comment": "Backfill passes explicit seasons; regular runs use the competition's configured season",
                        "Choices": [
                            {
                                "Variable": "$.seasons",
                                "IsNull": true,
                                "Next": "Use Competition Season"
                            }
                        ],
                        "Default": "Map Over Seasons"
                    },
                    "Use Competition Season": {
                        "Type": "Pass",
                        "Parameters": {
                            "competition.$": "$.competition",
                            "seasons.$": "States.Array($.competition.season)",
                            "mode.$": "$.mode",
                            "fixtures_only.$": "$.fixtures_only",
                            "run_id.$": "$.run_id",
                            "run_date.$": "$.run_date"
                        },
                        "Next": "Map Over Seasons"
                    },
                    "Map Over Seasons": {
                        "Type": "Map",
                        "MaxConcurrency": 1,
                        "ItemsPath": "$.seasons",
                        "ItemSelector": {
                            "league.$": "$.competition.league",
                            "type.$": "$.competition.type",
                            "has_standings.$": "$.competition.has_standings",
                            "season.$": "$$.Map.Item.Value",
                            "mode.$": "$.mode",
                            "fixtures_only.$": "$.fixtures_only",
                            "run_id.$": "$.run_id",
                            "run_date.$": "$.run_date"
                        },
                        "ItemProcessor": {
                            "ProcessorConfig": { "Mode": "INLINE" },
                            "StartAt": "Ingest Competition Season",
                            "States": {
                                "Ingest Competition Season": {
                                    "Type": "Parallel",
                                    "End": true,
                                    "Branches": [
                                        {
                                            "StartAt": "Get Fixtures",
                                            "States": {
                                                "Get Fixtures": {
                                                    "Type": "Task",
                                                    "Comment": "Stores the season fixture list; returns finished fixture ids (lookback window, or whole season in backfill) in chunks of 20",
                                                    "Resource": "arn:aws:states:::lambda:invoke",
                                                    "Parameters": {
                                                        "FunctionName": "${lambda_soccer_arn}",
                                                        "Payload": {
                                                            "action": "get_fixtures",
                                                            "league.$": "$.league",
                                                            "season.$": "$.season",
                                                            "mode.$": "$.mode",
                                                            "run_id.$": "$.run_id",
                                                            "run_date.$": "$.run_date"
                                                        }
                                                    },
                                                    "ResultSelector": {
                                                        "chunks.$": "$.Payload.result"
                                                    },
                                                    "ResultPath": "$.fixtures",
                                                    "Retry": [
                                                        {
                                                            "ErrorEquals": ["Lambda.ServiceException", "Lambda.AWSLambdaException", "Lambda.SdkClientException", "Lambda.TooManyRequestsException"],
                                                            "IntervalSeconds": 2,
                                                            "MaxAttempts": 3,
                                                            "BackoffRate": 2
                                                        },
                                                        {
                                                            "ErrorEquals": ["States.TaskFailed"],
                                                            "IntervalSeconds": 30,
                                                            "MaxAttempts": 1
                                                        }
                                                    ],
                                                    "Catch": [
                                                        {
                                                            "ErrorEquals": ["States.ALL"],
                                                            "Comment": "so other competitions continue; the error stays in the execution output",
                                                            "ResultPath": "$.error",
                                                            "Next": "Fixtures Failed"
                                                        }
                                                    ],
                                                    "Next": "Map Over Fixture Chunks"
                                                },
                                                "Map Over Fixture Chunks": {
                                                    "Type": "Map",
                                                    "MaxConcurrency": 2,
                                                    "ItemsPath": "$.fixtures.chunks",
                                                    "ItemSelector": {
                                                        "ids.$": "$$.Map.Item.Value.ids",
                                                        "league.$": "$.league",
                                                        "season.$": "$.season",
                                                        "run_id.$": "$.run_id",
                                                        "run_date.$": "$.run_date"
                                                    },
                                                    "ItemProcessor": {
                                                        "ProcessorConfig": { "Mode": "INLINE" },
                                                        "StartAt": "Get Fixture Details",
                                                        "States": {
                                                            "Get Fixture Details": {
                                                                "Type": "Task",
                                                                "Comment": "fixtures?ids= returns events, lineups, statistics and players for up to 20 matches",
                                                                "Resource": "arn:aws:states:::lambda:invoke",
                                                                "Parameters": {
                                                                    "FunctionName": "${lambda_soccer_arn}",
                                                                    "Payload": {
                                                                        "action": "get_fixture_details",
                                                                        "ids.$": "$.ids",
                                                                        "league.$": "$.league",
                                                                        "season.$": "$.season",
                                                                        "run_id.$": "$.run_id",
                                                                        "run_date.$": "$.run_date"
                                                                    }
                                                                },
                                                                "ResultSelector": {
                                                                    "result.$": "$.Payload.result"
                                                                },
                                                                "Retry": [
                                                                    {
                                                                        "ErrorEquals": ["Lambda.ServiceException", "Lambda.AWSLambdaException", "Lambda.SdkClientException", "Lambda.TooManyRequestsException"],
                                                                        "IntervalSeconds": 2,
                                                                        "MaxAttempts": 3,
                                                                        "BackoffRate": 2
                                                                    },
                                                                    {
                                                                        "ErrorEquals": ["States.TaskFailed"],
                                                                        "IntervalSeconds": 30,
                                                                        "MaxAttempts": 1
                                                                    }
                                                                ],
                                                                "Catch": [
                                                                    {
                                                                        "ErrorEquals": ["States.ALL"],
                                                                        "ResultPath": "$.error",
                                                                        "Next": "Fixture Details Failed"
                                                                    }
                                                                ],
                                                                "End": true
                                                            },
                                                            "Fixture Details Failed": {
                                                                "Type": "Pass",
                                                                "Parameters": {
                                                                    "failed": "get_fixture_details",
                                                                    "ids.$": "$.ids",
                                                                    "error.$": "$.error"
                                                                },
                                                                "End": true
                                                            }
                                                        }
                                                    },
                                                    "End": true
                                                },
                                                "Fixtures Failed": {
                                                    "Type": "Pass",
                                                    "Parameters": {
                                                        "failed": "get_fixtures",
                                                        "league.$": "$.league",
                                                        "season.$": "$.season",
                                                        "error.$": "$.error"
                                                    },
                                                    "End": true
                                                }
                                            }
                                        },
                                        {
                                            "StartAt": "Standings Needed?",
                                            "States": {
                                                "Standings Needed?": {
                                                    "Type": "Choice",
                                                    "Choices": [
                                                        {
                                                            "And": [
                                                                { "Variable": "$.has_standings", "BooleanEquals": true },
                                                                { "Variable": "$.fixtures_only", "BooleanEquals": false }
                                                            ],
                                                            "Next": "Get Standings"
                                                        }
                                                    ],
                                                    "Default": "Skip Standings"
                                                },
                                                "Get Standings": {
                                                    "Type": "Task",
                                                    "Resource": "arn:aws:states:::lambda:invoke",
                                                    "Parameters": {
                                                        "FunctionName": "${lambda_soccer_arn}",
                                                        "Payload": {
                                                            "action": "get_standings",
                                                            "league.$": "$.league",
                                                            "season.$": "$.season",
                                                            "run_id.$": "$.run_id",
                                                            "run_date.$": "$.run_date"
                                                        }
                                                    },
                                                    "ResultSelector": {
                                                        "result.$": "$.Payload.result"
                                                    },
                                                    "Retry": [
                                                        {
                                                            "ErrorEquals": ["Lambda.ServiceException", "Lambda.AWSLambdaException", "Lambda.SdkClientException", "Lambda.TooManyRequestsException"],
                                                            "IntervalSeconds": 2,
                                                            "MaxAttempts": 3,
                                                            "BackoffRate": 2
                                                        },
                                                        {
                                                            "ErrorEquals": ["States.TaskFailed"],
                                                            "IntervalSeconds": 30,
                                                            "MaxAttempts": 1
                                                        }
                                                    ],
                                                    "Catch": [
                                                        {
                                                            "ErrorEquals": ["States.ALL"],
                                                            "ResultPath": "$.error",
                                                            "Next": "Standings Failed"
                                                        }
                                                    ],
                                                    "End": true
                                                },
                                                "Standings Failed": {
                                                    "Type": "Pass",
                                                    "Parameters": {
                                                        "failed": "get_standings",
                                                        "league.$": "$.league",
                                                        "season.$": "$.season",
                                                        "error.$": "$.error"
                                                    },
                                                    "End": true
                                                },
                                                "Skip Standings": {
                                                    "Type": "Pass",
                                                    "Result": { "skipped": "standings" },
                                                    "End": true
                                                }
                                            }
                                        },
                                        {
                                            "StartAt": "Players Needed?",
                                            "States": {
                                                "Players Needed?": {
                                                    "Type": "Choice",
                                                    "Comment": "Player season stats only for domestic league teams; their cup/UCL stats come in the same response",
                                                    "Choices": [
                                                        {
                                                            "And": [
                                                                { "Variable": "$.type", "StringEquals": "league" },
                                                                { "Variable": "$.fixtures_only", "BooleanEquals": false }
                                                            ],
                                                            "Next": "Get Teams"
                                                        }
                                                    ],
                                                    "Default": "Skip Players"
                                                },
                                                "Get Teams": {
                                                    "Type": "Task",
                                                    "Resource": "arn:aws:states:::lambda:invoke",
                                                    "Parameters": {
                                                        "FunctionName": "${lambda_soccer_arn}",
                                                        "Payload": {
                                                            "action": "get_teams",
                                                            "league.$": "$.league",
                                                            "season.$": "$.season",
                                                            "run_id.$": "$.run_id",
                                                            "run_date.$": "$.run_date"
                                                        }
                                                    },
                                                    "ResultSelector": {
                                                        "items.$": "$.Payload.result"
                                                    },
                                                    "ResultPath": "$.teams",
                                                    "Retry": [
                                                        {
                                                            "ErrorEquals": ["Lambda.ServiceException", "Lambda.AWSLambdaException", "Lambda.SdkClientException", "Lambda.TooManyRequestsException"],
                                                            "IntervalSeconds": 2,
                                                            "MaxAttempts": 3,
                                                            "BackoffRate": 2
                                                        },
                                                        {
                                                            "ErrorEquals": ["States.TaskFailed"],
                                                            "IntervalSeconds": 30,
                                                            "MaxAttempts": 1
                                                        }
                                                    ],
                                                    "Catch": [
                                                        {
                                                            "ErrorEquals": ["States.ALL"],
                                                            "ResultPath": "$.error",
                                                            "Next": "Teams Failed"
                                                        }
                                                    ],
                                                    "Next": "Map Over Teams"
                                                },
                                                "Map Over Teams": {
                                                    "Type": "Map",
                                                    "MaxConcurrency": 5,
                                                    "ItemsPath": "$.teams.items",
                                                    "ItemSelector": {
                                                        "team.$": "$$.Map.Item.Value.team",
                                                        "season.$": "$.season",
                                                        "run_id.$": "$.run_id",
                                                        "run_date.$": "$.run_date"
                                                    },
                                                    "ItemProcessor": {
                                                        "ProcessorConfig": { "Mode": "INLINE" },
                                                        "StartAt": "Get Team Players",
                                                        "States": {
                                                            "Get Team Players": {
                                                                "Type": "Task",
                                                                "Resource": "arn:aws:states:::lambda:invoke",
                                                                "Parameters": {
                                                                    "FunctionName": "${lambda_soccer_arn}",
                                                                    "Payload": {
                                                                        "action": "get_team_players",
                                                                        "team.$": "$.team",
                                                                        "season.$": "$.season",
                                                                        "run_id.$": "$.run_id",
                                                                        "run_date.$": "$.run_date"
                                                                    }
                                                                },
                                                                "ResultSelector": {
                                                                    "result.$": "$.Payload.result"
                                                                },
                                                                "Retry": [
                                                                    {
                                                                        "ErrorEquals": ["Lambda.ServiceException", "Lambda.AWSLambdaException", "Lambda.SdkClientException", "Lambda.TooManyRequestsException"],
                                                                        "IntervalSeconds": 2,
                                                                        "MaxAttempts": 3,
                                                                        "BackoffRate": 2
                                                                    },
                                                                    {
                                                                        "ErrorEquals": ["States.TaskFailed"],
                                                                        "IntervalSeconds": 30,
                                                                        "MaxAttempts": 1
                                                                    }
                                                                ],
                                                                "Catch": [
                                                                    {
                                                                        "ErrorEquals": ["States.ALL"],
                                                                        "ResultPath": "$.error",
                                                                        "Next": "Team Players Failed"
                                                                    }
                                                                ],
                                                                "End": true
                                                            },
                                                            "Team Players Failed": {
                                                                "Type": "Pass",
                                                                "Parameters": {
                                                                    "failed": "get_team_players",
                                                                    "team.$": "$.team",
                                                                    "season.$": "$.season",
                                                                    "error.$": "$.error"
                                                                },
                                                                "End": true
                                                            }
                                                        }
                                                    },
                                                    "End": true
                                                },
                                                "Teams Failed": {
                                                    "Type": "Pass",
                                                    "Parameters": {
                                                        "failed": "get_teams",
                                                        "league.$": "$.league",
                                                        "season.$": "$.season",
                                                        "error.$": "$.error"
                                                    },
                                                    "End": true
                                                },
                                                "Skip Players": {
                                                    "Type": "Pass",
                                                    "Result": { "skipped": "players" },
                                                    "End": true
                                                }
                                            }
                                        }
                                    ]
                                }
                            }
                        },
                        "End": true
                    }
                }
            },
            "End": true
        }
    }
}
