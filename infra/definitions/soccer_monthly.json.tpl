{
    "Comment": "Monthly soccer ingestion of slow-changing data: league metadata/coverage, teams per competition and squads of domestic league teams. Input (optional): {\"competitions\": [...]}",
    "StartAt": "Set Defaults",
    "States": {
        "Set Defaults": {
            "Type": "Pass",
            "Parameters": {
                "defaults": {
                    "competitions": ${competitions}
                },
                "input.$": "$"
            },
            "Next": "Merge Input"
        },
        "Merge Input": {
            "Type": "Pass",
            "Parameters": {
                "config.$": "States.JsonMerge($.defaults, $.input, false)",
                "run_id.$": "$$.Execution.Name",
                "run_date.$": "States.ArrayGetItem(States.StringSplit($$.Execution.StartTime, 'T'), 0)"
            },
            "Next": "Map Over Competitions"
        },
        "Map Over Competitions": {
            "Type": "Map",
            "MaxConcurrency": 3,
            "ItemsPath": "$.config.competitions",
            "ItemSelector": {
                "league.$": "$$.Map.Item.Value.league",
                "type.$": "$$.Map.Item.Value.type",
                "season.$": "$$.Map.Item.Value.season",
                "run_id.$": "$.run_id",
                "run_date.$": "$.run_date"
            },
            "ItemProcessor": {
                "ProcessorConfig": { "Mode": "INLINE" },
                "StartAt": "Get League",
                "States": {
                    "Get League": {
                        "Type": "Task",
                        "Comment": "Result includes current_season: compare it with the configured season in the execution output",
                        "Resource": "arn:aws:states:::lambda:invoke",
                        "Parameters": {
                            "FunctionName": "${lambda_soccer_arn}",
                            "Payload": {
                                "action": "get_leagues",
                                "league.$": "$.league",
                                "run_id.$": "$.run_id",
                                "run_date.$": "$.run_date"
                            }
                        },
                        "ResultSelector": {
                            "current_season.$": "$.Payload.result.current_season"
                        },
                        "ResultPath": "$.league_info",
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
                                "Next": "Competition Failed"
                            }
                        ],
                        "Next": "Get Teams"
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
                                "Next": "Competition Failed"
                            }
                        ],
                        "Next": "Squads Needed?"
                    },
                    "Squads Needed?": {
                        "Type": "Choice",
                        "Comment": "Cup team lists can hold hundreds of lower-division teams; squads only for domestic league teams",
                        "Choices": [
                            {
                                "Variable": "$.type",
                                "StringEquals": "league",
                                "Next": "Map Over Teams"
                            }
                        ],
                        "Default": "No Squads"
                    },
                    "No Squads": {
                        "Type": "Pass",
                        "Result": [],
                        "ResultPath": "$.squads",
                        "Next": "Competition Done"
                    },
                    "Map Over Teams": {
                        "Type": "Map",
                        "MaxConcurrency": 5,
                        "ItemsPath": "$.teams.items",
                        "ItemSelector": {
                            "team.$": "$$.Map.Item.Value.team",
                            "run_id.$": "$.run_id",
                            "run_date.$": "$.run_date"
                        },
                        "ItemProcessor": {
                            "ProcessorConfig": { "Mode": "INLINE" },
                            "StartAt": "Get Squad",
                            "States": {
                                "Get Squad": {
                                    "Type": "Task",
                                    "Resource": "arn:aws:states:::lambda:invoke",
                                    "Parameters": {
                                        "FunctionName": "${lambda_soccer_arn}",
                                        "Payload": {
                                            "action": "get_squad",
                                            "team.$": "$.team",
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
                                            "Next": "Squad Failed"
                                        }
                                    ],
                                    "End": true
                                },
                                "Squad Failed": {
                                    "Type": "Pass",
                                    "Parameters": {
                                        "failed": "get_squad",
                                        "team.$": "$.team",
                                        "error.$": "$.error"
                                    },
                                    "End": true
                                }
                            }
                        },
                        "ResultPath": "$.squads",
                        "Next": "Competition Done"
                    },
                    "Competition Done": {
                        "Type": "Pass",
                        "Parameters": {
                            "league.$": "$.league",
                            "configured_season.$": "$.season",
                            "current_season.$": "$.league_info.current_season",
                            "teams.$": "States.ArrayLength($.teams.items)",
                            "squads.$": "$.squads"
                        },
                        "End": true
                    },
                    "Competition Failed": {
                        "Type": "Pass",
                        "Parameters": {
                            "failed": "monthly",
                            "league.$": "$.league",
                            "season.$": "$.season",
                            "error.$": "$.error"
                        },
                        "End": true
                    }
                }
            },
            "End": true
        }
    }
}
