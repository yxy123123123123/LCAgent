#pragma once

#include "CoreMinimal.h"
#include "MCPTCPServer.h"
#include "Engine/World.h"
#include "RoadAuto/Private/UI/RoadAutomationUI.h"
#include "MCPCommandHandlers.h"

/**
 * Handler for Road Automation commands
 */
class FMCPRoadAutoCommandHandler : public FMCPCommandHandlerBase
{
public:
    /**
     * Constructor
     */
    FMCPRoadAutoCommandHandler()
        : FMCPCommandHandlerBase("RoadAuto")
        , RoadAutomationUI(nullptr)
    {
    }

    /**
     * Execute the road automation command
     * @param Params - Command parameters
     * @param ClientSocket - Client socket for response
     * @return JSON response object
     */
    virtual TSharedPtr<FJsonObject> Execute(const TSharedPtr<FJsonObject>& Params, FSocket* ClientSocket) override;

private:
    /**
     * Set actor or blueprint class for road generation
     * @param ClassPath - Path to the actor/blueprint class
     * @return Success status
     */
    bool SetActorOrBlueprintClass(const FString& ClassPath);

    /**
     * Set paint layer for terrain deformation
     * @param LayerPath - Path to the paint layer
     * @return Success status
     */
    bool SetPaintLayer(const FString& LayerPath);

    /**
     * Set points from positions for road path
     * @param Positions - Array of position coordinates
     * @return Success status
     */
    bool SetPoints(const TArray<FVector>& Positions);

    bool SetWidth(const float Width);

    /**
     * Execute road generation operation
     * @return Success status
     */
    bool ExecuteGeneration();

    /**
     * Create response JSON with operation results
     * @param Success - Operation success status
     * @param Message - Operation message
     * @param RoadData - Optional road data
     * @return JSON response object
     */
    TSharedPtr<FJsonObject> CreateResponseJson(bool Success, const FString& Message, const TSharedPtr<FJsonObject>& RoadData = nullptr);

    /**
     * Log operation information
     * @param Message - Log message
     */
    void LogOperation(const FString& Message);

    /**
     * Log error information
     * @param Message - Error message
     */
    void LogError(const FString& Message);

    /**
     * Log performance metrics
     * @param Operation - Operation name
     * @param Duration - Operation duration
     */
    void LogPerformanceMetrics(const FString& Operation, float Duration);

    /**
     * Log detailed error with function name and line number
     * @param FunctionName - Function name
     * @param FileName - File name
     * @param LineNumber - Line number
     * @param Message - Error message
     */
    void LogDetailedError(const FString& FunctionName, const FString& FileName, int32 LineNumber, const FString& Message);

    /**
     * Log memory usage statistics
     */
    void LogMemoryUsage();

    /**
     * Clean up resources
     */
    void CleanupResources();

    /**
     * Optimize memory usage
     */
    void OptimizeMemoryUsage();

private:
    
    /** Road Automation UI instance */
    TSharedPtr<FRoadAutomationUI> RoadAutomationUI;
};