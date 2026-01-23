{
    "Comment": "Soccer Data Ingestion Workflow",
    "StartAt": "Set Parameters",
    "States": {
        "Set Parameters": {
            "Type": "Pass",
            "Parameters": {
                "array_tuple_league_season": ${setup_array_tuple}
            },
            "Comment": "Sets the parameters for different leagues and seasons",
            "Next": "Map Over Leagues and Seasons"
        },
        "Map Over Leagues and Seasons": {
            "Type": "Map",
            "ItemsPath": "$.array_tuple_league_season",
            "Parameters": {
                "league.$": "$$.Map.Item.Value.league",
                "season.$": "$$.Map.Item.Value.season"
            },
            "Iterator": {
                "StartAt": "Get Teams for League and Season",
                "States": {
                    "Get Teams for League and Season": {
                        "Type": "Task",
                        "Resource": "arn:aws:states:::lambda:invoke",
                        "Parameters": {
                            "FunctionName": "${lambda_soccer_arn}",
                            "Payload": {
                                "league.$": "$.league",
                                "season.$": "$.season",
                                "action": "get_teams_from_league"
                            }
                        },
                        "Next": "Map Over Teams"
                    },
                    "Map Over Teams": {
                        "Type": "Map",
                        "ItemsPath": "States.StringToJson($.Payload.teams)",
                        "Parameters": {
                            "league.$": "$.league",
                            "season.$": "$.season",
                            "team_id.$": "$$.Map.Item.Value.team"
                        },
                        "Iterator": {
                            "StartAt": "Get teams stats and players stats",
                            "States": {
                                "Get teams stats and players stats": {
                                    "Type": "Parallel",
                                    "Branches": [
                                        {
                                            "StartAt": "Fetch Team Stats",
                                            "States": {
                                                "Fetch Team Stats": {
                                                    "Type": "Task",
                                                    "Resource": "arn:aws:states:::lambda:invoke",
                                                    "Parameters": {
                                                        "FunctionName": "${lambda_soccer_arn}",
                                                        "Payload": {
                                                            "league.$": "$.league",
                                                            "season.$": "$.season",
                                                            "team_id.$": "$.team_id",
                                                            "action": "get_team_stats"
                                                        }
                                                    },
                                                    "End": true
                                                }
                                            }
                                        },
                                        {
                                            "StartAt": "Fetch Players Stats",
                                            "States": {
                                                "Fetch Players Stats": {
                                                    "Type": "Task",
                                                    "Resource": "arn:aws:states:::lambda:invoke",
                                                    "Parameters": {
                                                        "FunctionName": "${lambda_soccer_arn}",
                                                        "Payload": {
                                                            "league.$": "$.league",
                                                            "season.$": "$.season",
                                                            "team_id.$": "$.team_id",
                                                            "action": "get_team_players"
                                                        }
                                                    },
                                                    "End": true
                                                }
                                            }
                                        }
                                    ],
                                    "End": true
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