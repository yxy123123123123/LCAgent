// Fill out your copyright notice in the Description page of Project Settings.

#pragma once

#include "CoreMinimal.h"
#include "MCPTCPServer.h"
#include "Engine/World.h"
#include "FoliageAuto/Private/UI/FoliageAutomationUI.h"
#include "MCPCommandHandlers.h"

/**
 * 
 */
class  FMCPFoliageAutoCommandHandler : public FMCPCommandHandlerBase

{
public:
    /**
     * Constructor
     */
    FMCPFoliageAutoCommandHandler()
        : FMCPCommandHandlerBase("FoliageAuto")
        , FoliageAutomationUI(nullptr)
    {
    }

    /**
     * Execute the River automation command
     * @param Params - Command parameters
     * @param ClientSocket - Client socket for response
     * @return JSON response object
     */
    virtual TSharedPtr<FJsonObject> Execute(const TSharedPtr<FJsonObject>& Params, FSocket* ClientSocket) override;

private:

    bool SetFoliageSpawner(const FString& AssetPath);

    bool SetFoliageLocation(TArray<FVector> Location);

    bool SetFoliageSize(TArray<FVector> Size);

    bool ExecuteGeneration();

    TSharedPtr<FJsonObject> CreateResponseJson(bool Success, const FString& Message, const TSharedPtr<FJsonObject>& RoadData = nullptr);

    void LogOperation(const FString& Message);

    void LogError(const FString& Message);

    void LogPerformanceMetrics(const FString& Operation, float Duration);

    void LogDetailedError(const FString& FunctionName, const FString& FileName, int32 LineNumber, const FString& Message);

    void LogMemoryUsage();

    void CleanupResources();

    void OptimizeMemoryUsage();

private:

    /** River Automation UI instance */
    TSharedPtr<FFoliageAutomationUI> FoliageAutomationUI;
};
