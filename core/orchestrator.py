from typing import List
from core.base_agent import BaseCanvasAgent
from core.state import AgentCanvasState

class CanvasOrchestrator:
    def __init__(self, agents: List[BaseCanvasAgent]):
        self.agents = agents

    def run_pipeline(self, initial_state: AgentCanvasState) -> AgentCanvasState:
        state = initial_state
        print(f"--- Starting Pipeline for Domain: {state['domain_context']} ---")
        
        for agent in self.agents:
            print(f"Executing Agent: {agent.name}")
            state = agent.execute(state)
            state['current_step'] = agent.name
            
        print("--- Pipeline Completed Successfully ---")
        return state
