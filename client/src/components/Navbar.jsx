import React, { useState } from 'react'
import { Link } from 'react-router-dom'

const Navbar = () => {
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);

  const toggleMobileMenu = () => {
      setIsMobileMenuOpen(!isMobileMenuOpen);
  };

  return (
    <nav className='bg-[#005b39]'>
        <div className='w-full mx-auto px-4 sm:px-6 lg:px-8'>
            <div className='flex justify-between items-center py-4'>

                {/* Logo and Title Section */}
                <div className='flex items-center space-x-2 flex-shrink-0'>
                    <img className="h-12 sm:h-14 w-auto object-contain" src="logo_symbol_only.png" alt="transparent logo" />
                    
                    <div className="flex items-center">
                        <h1 className='font-lora text-xl sm:text-2xl text-[#f4c968]'>
                            SchedKo
                            <span className='text-white hidden sm:inline'> | </span>
                            <span className='font-jost text-[#fff1d3] text-base sm:text-xl md:text-2xl hidden sm:inline'>
                                Personalized Exam Schedule Generator
                            </span>
                        </h1>
                    </div>
                </div>

                {/* Navigation Links */}
                <div className="hidden md:block">
                    <div className="ml-4 lg:ml-10 flex items-center space-x-2 lg:space-x-4">
                        <Link to="/" className="text-lg lg:text-2xl font-jost text-[#f4c968] px-2 lg:px-3 py-2 rounded-md hover:bg-[#004d30] transition-colors duration-200">Home</Link>
                        <Link to="/about" className="text-lg lg:text-2xl font-jost text-[#f4c968] px-2 lg:px-3 py-2 rounded-md hover:bg-[#004d30] transition-colors duration-200">About</Link>
                        <Link to="/support" className="text-lg lg:text-2xl font-jost text-[#f4c968] px-2 lg:px-3 py-2 rounded-md hover:bg-[#004d30] transition-colors duration-200">Support</Link>
                    </div>
                </div>

                {/* Mobile Menu Button */}
                <div className="md:hidden">
                    <button 
                        onClick={toggleMobileMenu}
                        className="text-[#f4c968] p-2 rounded-md hover:bg-[#004d30] transition-colors duration-200"
                        aria-label="Toggle mobile menu"
                    >
                        <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
                        </svg>
                    </button>
                </div>
            </div>

            {/* Mobile Menu Dropdown */}
            <div className={`md:hidden ${isMobileMenuOpen ? 'block' : 'hidden'}`}>
                <div className="px-2 pt-2 pb-3 space-y-1 border-t border-[#004d30]">
                    <Link to="/" className="block text-lg font-jost text-[#f4c968] px-3 py-2 rounded-md hover:bg-[#004d30] transition-colors duration-200">Home</Link>
                    <Link to="/about" className="block text-lg font-jost text-[#f4c968] px-3 py-2 rounded-md hover:bg-[#004d30] transition-colors duration-200">About</Link>
                    <Link to="/support" className="block text-lg font-jost text-[#f4c968] px-3 py-2 rounded-md hover:bg-[#004d30] transition-colors duration-200">Support</Link>
                </div>
            </div>
        </div>
    </nav>
  )
}

export default Navbar
