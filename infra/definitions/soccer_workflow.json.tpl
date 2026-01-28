{
    "Comment": "Soccer Data Ingestion Workflow",
    "StartAt": "Set Parameters",
    "States": {
        "Set Parameters": {
            "Type": "Choice",
            "Comment": "Check if league and season are provided as input parameters",
            "Choices": [
                {
                    "And": [
                        {
                            "Variable": "$.league",
                            "IsPresent": true
                        },
                        {
                            "Variable": "$.season",
                            "IsPresent": true
                        }
                    ],
                    "Next": "Use Input Parameters"
                }
            ],
            "Default": "Use Setup Array"
        },
        "Use Input Parameters": {
            "Type": "Pass",
            "Parameters": {
                "array_tuple_league_season": [
                    {
                        "league.$": "$.league",
                        "season.$": "$.season"
                    }
                ]
            },
            "Comment": "Uses the provided league and season parameters",
            "Next": "Map Over Leagues and Seasons"
        },
        "Use Setup Array": {
            "Type": "Pass",
            "Parameters": {
                "array_tuple_league_season": ${setup_array_tuple}
            },
            "Comment": "Uses the setup_array_tuple configuration",
            "Next": "Map Over Leagues and Seasons"
        },
        "Map Over Leagues and Seasons": {
            "Type": "Map",
            "MaxConcurrency": 1,
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
                        "Next": "Parse Teams String"
                    },
                    "Parse Teams String": {
                        "Type": "Pass",
                        "Parameters": {
                            "teamsArray.$": "States.StringToJson($.Payload.teams)" 
                        },
                        "Next": "Map Over Teams"
                    },
                    "Map Over Teams": {
                        "Type": "Map",
                        "ItemsPath": "$.teamsArray",
                        "MaxConcurrency": 1,
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
                                                            "teamid.$": "$.team",
                                                            "action": "get_team_stats"
                                                        }
                                                    },
                                                    "End": true,
                                                    "Retry": [
                                                        {
                                                            "ErrorEquals":[
                                                                "Lambda.ServiceException",
                                                                "Lambda.AWSLambdaException",
                                                                "Lambda.SdkClientException"
                                                            ],
                                                            "IntervalSeconds": 2,
                                                            "MaxAttempts": 1
                                                        }
                                                    ],
                                                    "Catch": [
                                                        {
                                                            "ErrorEquals": [
                                                                "States.ALL"
                                                            ],
                                                            "Comment": "so others iterations don't fail on error",
                                                            "Next": "Dummy Failure 1"
                                                        }
                                                    ]
                                                },
                                                "Dummy Failure 1" : {
                                                    "Type" : "Succeed"
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
                                                            "teamid.$": "$.team",
                                                            "action": "get_team_players"
                                                        }
                                                    },
                                                    "End": true,
                                                    "Retry": [
                                                        {
                                                            "ErrorEquals":[
                                                                "Lambda.ServiceException",
                                                                "Lambda.AWSLambdaException",
                                                                "Lambda.SdkClientException"
                                                            ],
                                                            "IntervalSeconds": 2,
                                                            "MaxAttempts": 1
                                                        }
                                                    ],
                                                    "Catch": [
                                                        {
                                                            "ErrorEquals": [
                                                                "States.ALL"
                                                            ],
                                                            "Comment": "so others iterations don't fail on error",
                                                            "Next": "Dummy Failure"
                                                        }
                                                    ]
                                                },
                                                "Dummy Failure" : {
                                                    "Type" : "Succeed"
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